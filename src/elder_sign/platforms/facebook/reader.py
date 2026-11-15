"""Read the person's own reels on Facebook, as they would in their browser.

`FacebookPlatform` is the Facebook adapter: the `Platform` the scan loop uses.
It reads through a `Browser`, which opens pages and records what Facebook
sends for them, and makes sense of what was recorded with `parse`. The real
browser is `PlaywrightBrowser`; the tests give it a stand-in that replays a
capture, so everything here but the clicking is proven without Facebook.

- **Recent reels** are listed from the profile's reels tab, newest first.
  Each is opened, once, for when it was published; the listing stops after a
  few reels older than the window, in case one is pinned out of order.
- **A fingerprint** is read from the reel's own page: its comment count,
  replies included, and the newest of the comments the page loads with it.
  It is read from the visit the listing made when there was one, and from a
  fresh visit after that, so a fingerprint taken after cleaning is the
  reel as it is then.
- **A scan** opens the reel, shows all comments rather than the most relevant,
  and opens every "View more comments" and "View replies" there is. Facebook
  plays other people's reels beside the person's, so only the comments on the
  reel's own post are kept.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from elder_sign.browser import BrowserError, BrowserSettings, Pacer, open_browser
from elder_sign.platforms.base import (
    FetchedMedia,
    MediaItem,
    Post,
    Scan,
    fingerprint_of,
    validate_scan,
)
from elder_sign.platforms.facebook.capture import Recorder
from elder_sign.platforms.facebook.parse import (
    REEL_PATH,
    PostDetails,
    comments_in,
    post_id_of,
    posts_in,
)
from elder_sign.platforms.facebook.session import signed_in_account
from elder_sign.shared.store import Fingerprint

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page

PLATFORM: Final = "facebook"

REELS_TAB: Final = "https://www.facebook.com/profile.php?id={account}&sk=reels_tab"
"""The person's own reels, newest first."""

REEL_URL: Final = "https://www.facebook.com/reel/{reel_id}/"

OLDER_TOLERANCE: Final = 3
"""How many reels older than the window are read before the listing stops."""

Records = list[dict[str, Any]]


class Browser(Protocol):
    """What the Facebook adapter needs from a browser."""

    def visit(
        self,
        url: str,
    ) -> Records:
        """Open a page, and return what Facebook sent for it."""

    def expand(self) -> Records:
        """Open every folded comment and reply on the open page, and return what was sent."""

    def reel_links(
        self,
        url: str,
    ) -> list[str]:
        """Return the links to reels on a page, in the order it shows them."""


class FacebookPlatform:
    """The person's own reels on Facebook, read through a browser."""

    name = PLATFORM

    def __init__(
        self,
        browser: Browser,
        account_id: str,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        older_tolerance: int = OLDER_TOLERANCE,
    ) -> None:
        self._browser = browser
        self._account_id = account_id
        self._now = now
        self._older_tolerance = older_tolerance
        self._visits: dict[str, Records] = {}

    def _visit(
        self,
        reel_id: str,
    ) -> Records:
        """Return what one visit to a reel's page sent, using a visit made earlier only once."""

        if reel_id in self._visits:
            return self._visits.pop(reel_id)

        return self._browser.visit(REEL_URL.format(reel_id=reel_id))

    @staticmethod
    def _details(
        reel_id: str,
        records: Records,
    ) -> PostDetails | None:
        """Return what a reel's page says of the reel's own post."""

        for details in posts_in(records).values():
            if details.reel_id == reel_id:
                return details

        return None

    def recent_posts(
        self,
        days: int,
    ) -> Sequence[Post]:
        """Return the person's reels published in the last `days` days, newest first."""

        since = self._now() - timedelta(days=days)
        links = self._browser.reel_links(REELS_TAB.format(account=self._account_id))
        reel_ids = list(
            dict.fromkeys(match.group(1) for link in links if (match := REEL_PATH.search(link)))
        )
        posts: list[Post] = []
        older = 0

        for reel_id in reel_ids:
            records = self._browser.visit(REEL_URL.format(reel_id=reel_id))
            details = self._details(reel_id, records)
            created = details.created if details else None

            if created is not None and created < since:
                older += 1

                if older >= self._older_tolerance:
                    break

                continue

            self._visits[reel_id] = records
            posts.append(
                Post(
                    platform=PLATFORM,
                    post_id=reel_id,
                    url=REEL_URL.format(reel_id=reel_id),
                    published_at=created,
                )
            )

        return posts

    def _own_comments(
        self,
        reel_id: str,
        records: Records,
    ) -> Scan:
        """Return the comments on a reel's own post, of all a page's records hold."""

        details = self._details(reel_id, records)
        comments = comments_in(records)

        if details is not None:
            comments = [
                comment
                for comment in comments
                if post_id_of(comment["id"]) in (details.post_id, None)
            ]

        return validate_scan({"platform": PLATFORM, "post_id": reel_id, "comments": comments})

    def fingerprint(
        self,
        post: Post,
    ) -> Fingerprint:
        """Return the reel's comment count, and the newest comment its page loads with it."""

        records = self._visit(post.post_id)
        details = self._details(post.post_id, records)
        loaded = self._own_comments(post.post_id, records)
        newest = fingerprint_of(loaded).newest_comment_id
        count = details.comment_count if details and details.comment_count is not None else None

        return Fingerprint(
            comment_count=count if count is not None else len(loaded["comments"]),
            newest_comment_id=newest,
        )

    def scan(
        self,
        post: Post,
    ) -> Scan:
        """Return every comment and reply on the reel, every folded one opened."""

        self._visits.pop(post.post_id, None)
        records = self._browser.visit(post.url) + self._browser.expand()

        return self._own_comments(post.post_id, records)

    def fetch_media(
        self,
        item: MediaItem,
    ) -> FetchedMedia | None:
        """Return nothing yet: media is fetched once the browser can download it."""

        return None


EXPANDERS: Final = (
    re.compile(r"^View (?:all |more |previous )?(?:\d+ )?(?:more )?comments?\b", re.IGNORECASE),
    re.compile(r"^View (?:all |more )?(?:\d+ )?(?:more )?repl(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"^\d+ (?:more )?repl(?:y|ies)$", re.IGNORECASE),
    re.compile(r"^See more comments\b", re.IGNORECASE),
)
"""The names of the buttons that open folded comments and replies."""

SORTING: Final = re.compile(r"^(?:Most relevant|Newest|All comments|Top comments)$", re.IGNORECASE)
"""The name of the button that chooses which comments a reel shows."""

QUIET_SCROLLS: Final = 2
"""How many scrolls that show no new reel end the listing."""

SCROLL_MILLISECONDS: Final = 800
"""How long a scroll is given to show what it loads, before the page is looked at again."""

STALE_CLICKS: Final = 2
"""How many clicks of one button that bring nothing new before it is given up on."""

ALL_COMMENTS: Final = re.compile(r"^All comments\b", re.IGNORECASE)
"""The choice that shows every comment, rather than only those Facebook ranks highest."""


class PlaywrightBrowser:
    """The real browser: Elder Sign's Chrome profile, paced, recording what Facebook sends.

    Buttons are found by their accessible names, as a screen reader finds
    them, not by Facebook's markup, which changes from week to week.
    """

    def __init__(
        self,
        context: "BrowserContext",
        pacer: Pacer,
        *,
        max_expansions: int = 200,
        max_scrolls: int = 20,
        settle_milliseconds: int = 4000,
    ) -> None:
        self._recorder = Recorder()
        self._pacer = pacer
        self._max_expansions = max_expansions
        self._max_scrolls = max_scrolls
        self._settle_milliseconds = settle_milliseconds
        context.on("response", self._recorder.observe_response)
        self._page: Page = context.pages[0] if context.pages else context.new_page()

    def _since(
        self,
        start: int,
    ) -> Records:
        """Return what was recorded since a point."""

        return list(self._recorder.records[start:])

    def _settle(self) -> None:
        """Wait until the page has quietened, or for as long as that is waited for."""

        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with suppress(PlaywrightTimeout):
            self._page.wait_for_load_state("networkidle", timeout=self._settle_milliseconds)

    def visit(
        self,
        url: str,
    ) -> Records:
        """Open a page, wait a human while, and return what Facebook sent for it."""

        start = len(self._recorder.records)
        self._page.goto(url, wait_until="domcontentloaded")
        self._settle()
        self._pacer.pause()

        return self._since(start)

    def _visible(
        self,
        role: Literal["button", "menuitem"],
        name: re.Pattern[str],
        skip: Counter[str] | None = None,
    ) -> "Locator | None":
        """Return the first visible element of a role and name, if there is one.

        An element whose text is in `skip` as often as `STALE_CLICKS` is passed
        over: clicking it has stopped bringing anything new.
        """

        found = self._page.get_by_role(role, name=name)

        for index in range(found.count()):
            element = found.nth(index)

            if not element.is_visible():
                continue

            if skip is not None and skip[element.inner_text()] >= STALE_CLICKS:
                continue

            return element

        return None

    def _show_all_comments(self) -> None:
        """Choose to see every comment, when the reel offers the choice."""

        sorting = self._visible("button", SORTING)

        if sorting is None:
            return

        sorting.click()
        self._pacer.pause(0.5)
        everything = self._visible("menuitem", ALL_COMMENTS)

        if everything is not None:
            everything.click()
            self._settle()
            self._pacer.pause(0.5)

    def expand(self) -> Records:
        """Open every folded comment and reply, paced, and return what was sent while doing it."""

        start = len(self._recorder.records)
        self._show_all_comments()
        stale: Counter[str] = Counter()

        for _ in range(self._max_expansions):
            button = next(
                (
                    found
                    for pattern in EXPANDERS
                    if (found := self._visible("button", pattern, stale)) is not None
                ),
                None,
            )

            if button is None:
                break

            text = button.inner_text()
            before = len(self._recorder.records)
            button.click()
            self._settle()
            self._pacer.pause(0.6)

            if len(self._recorder.records) == before:
                stale[text] += 1

        return self._since(start)

    def reel_links(
        self,
        url: str,
    ) -> list[str]:
        """Return the links to reels a page shows, scrolling until no more appear."""

        self._page.goto(url, wait_until="domcontentloaded")
        self._settle()
        links: list[str] = []
        quiet = 0

        for _ in range(self._max_scrolls):
            found = self._page.eval_on_selector_all(
                "a[href*='/reel/']", "elements => elements.map(element => element.href)"
            )
            fresh = [link for link in found if isinstance(link, str) and link not in links]
            quiet = 0 if fresh else quiet + 1

            if quiet >= QUIET_SCROLLS:
                break

            links.extend(fresh)
            self._page.mouse.wheel(0, 2400)
            self._page.wait_for_timeout(SCROLL_MILLISECONDS)
            self._pacer.pause(0.5)
            self._settle()

        return links


NOT_SIGNED_IN: Final = "Elder Sign is not signed in to Facebook; run: elder-sign login facebook"


@contextmanager
def facebook(
    settings: BrowserSettings | None = None,
    pacer: Pacer | None = None,
) -> Iterator[FacebookPlatform]:
    """Open Facebook in Elder Sign's browser, as the account it is signed in to.

    Raises:
        BrowserError: The browser cannot be opened, or is not signed in to Facebook.
    """

    with open_browser(settings) as context:
        account = signed_in_account(context)

        if account is None:
            raise BrowserError(NOT_SIGNED_IN)

        yield FacebookPlatform(PlaywrightBrowser(context, pacer or Pacer()), account)
