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
  and opens every "View more comments" and "View replies" there is; then it
  shows the comments "Hidden by You", by the person or by their keyword
  filter, and opens those. Facebook plays other people's reels beside the
  person's, so only the comments on the reel's own post are kept. The scan
  says how many comments Facebook counts, so what was read can be checked
  against it: Facebook counts comments it never serves, such as replies
  orphaned by a deleted comment and comments of blocked accounts.
- **Notices** Facebook gives of comments it filtered out of view are those
  it gave while all comments were shown. The ones it gives with the reel's
  page, before all are chosen, and with "Hidden by You" describe views the
  scan left or chose, not what it read.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    filtering_notices,
    post_id_of,
    posts_in,
)
from elder_sign.platforms.facebook.session import signed_in_account
from elder_sign.shared.store import Fingerprint

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page, Request, Response

PLATFORM: Final = "facebook"

REELS_TAB: Final = "https://www.facebook.com/profile.php?id={account}&sk=reels_tab"
"""The person's own reels, newest first."""

REEL_URL: Final = "https://www.facebook.com/reel/{reel_id}/"

OLDER_TOLERANCE: Final = 3
"""How many reels older than the window are read before the listing stops."""

Records = list[dict[str, Any]]

Stage = Literal["visit", "opening", "shown", "hidden"]
"""What a page was showing when the browser asked Facebook for something."""


@dataclass(frozen=True)
class Expansion:
    """What Facebook sent while a reel's comments were opened, by what they showed."""

    opening: Records = field(default_factory=list)
    """Sent as the comments opened, before all of them were chosen: Facebook's own ranking."""

    shown: Records = field(default_factory=list)
    """Sent while all comments were shown and opened, or by default when there is no choice."""

    hidden: Records = field(default_factory=list)
    """Sent while the comments hidden by the person, or by their keyword filter, were shown."""

    @property
    def records(self) -> Records:
        """Everything sent, in the order it was."""

        return [*self.opening, *self.shown, *self.hidden]


class Browser(Protocol):
    """What the Facebook adapter needs from a browser."""

    def visit(
        self,
        url: str,
    ) -> Records:
        """Open a page, and return what Facebook sent for it."""

    def expand(
        self,
        comment_count: int | None = None,
    ) -> Expansion:
        """Open every folded comment and reply on the open page, and return what was sent.

        `comment_count` is how many comments the reel has, when it is known, so
        its own comments are opened rather than those of the next reel playing.
        """

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
        self.notices: list[str] = []
        """What Facebook said, in the last scan, of comments it filtered out of view."""

    def _visit(
        self,
        reel_id: str,
    ) -> Records:
        """Return what one visit to a reel's page sent, using a visit made earlier only once."""

        if reel_id in self._visits:
            return self._visits.pop(reel_id)

        return self._browser.visit(REEL_URL.format(reel_id=reel_id))

    @staticmethod
    def details_of(
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
            details = self.details_of(reel_id, records)
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

        details = self.details_of(reel_id, records)
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
        details = self.details_of(post.post_id, records)
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
        visited = self._browser.visit(post.url)
        details = self.details_of(post.post_id, visited)
        count = details.comment_count if details else None
        expansion = self._browser.expand(count)
        self.notices = filtering_notices(expansion.shown)
        scan = self._own_comments(post.post_id, visited + expansion.records)
        scan["comment_count"] = count

        return scan

    def fetch_media(
        self,
        item: MediaItem,
    ) -> FetchedMedia | None:
        """Return nothing yet: media is fetched once the browser can download it."""

        return None


EXPANDERS: Final = (
    re.compile(
        r"^(?:View|See|Show) (?:all |more )?(?:\d+ )?(?:hidden|filtered) "
        r"(?:comments?|repl(?:y|ies))\b",
        re.IGNORECASE,
    ),
    re.compile(r"^View (?:all |more |previous )?(?:\d+ )?(?:more )?comments?\b", re.IGNORECASE),
    re.compile(r"^View (?:all |more )?(?:\d+ )?(?:more )?repl(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"^\d+ (?:more )?repl(?:y|ies)$", re.IGNORECASE),
    re.compile(r"^See more comments\b", re.IGNORECASE),
)
"""The names of the buttons that open folded comments and replies."""

OPEN_COMMENTS: Final = re.compile(
    r"^(?:Comments?(?:, [\d,.]+K? comments?)?|[\d,.]+K? comments?)$", re.IGNORECASE
)
"""The name of the button that opens a reel's comments, beside its like and share buttons.

Facebook names it with its count, `Comment, 5 comments`, and names the next
reel playing beside it the same way, `Comment, 192 comments`.
"""


def opens_comments(
    comment_count: int,
) -> re.Pattern[str]:
    """Return the name of the button that opens the comments of a reel with this many."""

    noun = "comment" if comment_count == 1 else "comments"

    return re.compile(rf"^Comments?, {comment_count:,} {noun}$", re.IGNORECASE)


SORTING: Final = re.compile(
    r"^(?:Most relevant|Newest|All comments|Top comments|Hidden by You)$", re.IGNORECASE
)
"""The name of the button that chooses which comments a reel shows."""

QUIET_SCROLLS: Final = 2
"""How many scrolls that show no new reel end the listing."""

SCROLL_MILLISECONDS: Final = 800
"""How long a scroll is given to show what it loads, before the page is looked at again."""

STALE_CLICKS: Final = 2
"""How many clicks of one button that bring nothing new before it is given up on."""

HIDDEN_BY_YOU: Final = re.compile(r"^Hidden by You\b", re.IGNORECASE)
"""The choice that shows the comments the person hid, or their keyword filter hid for them."""

ALL_COMMENTS: Final = re.compile(r"^All comments\b", re.IGNORECASE)
"""The choice that shows every comment, rather than only those Facebook ranks highest."""


class PlaywrightBrowser:
    """The real browser: Elder Sign's Chrome profile, paced, recording what Facebook sends.

    Buttons are found by their accessible names, as a screen reader finds
    them, not by Facebook's markup, which changes from week to week.

    Each response is known by the stage its request was sent in, not the one
    it arrived in: Facebook answers a click after the click has returned.
    """

    @property
    def records(self) -> Records:
        """Every response recorded since the browser was opened."""

        return self._recorder.records

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
        self._stage: Stage = "visit"
        self._asked: dict[Request, Stage] = {}
        self._stages: list[Stage] = []
        context.on("request", self._observe_request)
        context.on("response", self._observe_response)
        self._page: Page = context.pages[0] if context.pages else context.new_page()

    def _observe_request(
        self,
        request: "Request",
    ) -> None:
        """Note the stage a request is sent in."""

        self._asked[request] = self._stage

    def _observe_response(
        self,
        response: "Response",
    ) -> None:
        """Record a response, if it is kept, as of the stage its request was sent in.

        A response's body is read as it is recorded, and other responses can
        arrive meanwhile, so each is staged as it is recorded, in that order.
        """

        stage = self._asked.pop(response.request, self._stage)

        if self._recorder.keep_response(response):
            self._stages.append(stage)

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
        self._stage = "visit"
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

    def open_comments(
        self,
        comment_count: int | None = None,
    ) -> None:
        """Open the reel's comments, which its page keeps closed until they are asked for.

        With the reel's comment count, its own button is the one named with
        that count; failing that, or without it, the first on the page, which
        is the reel's own. Comments already open are left so: the button that
        opens them may close them.
        """

        if self._visible("button", SORTING) is not None or any(
            self._visible("button", pattern) is not None for pattern in EXPANDERS
        ):
            return

        button = None

        if comment_count is not None and comment_count < 1000:
            button = self._visible("button", opens_comments(comment_count))

        if button is None:
            button = self._visible("button", OPEN_COMMENTS)

        if button is None:
            return

        button.click()
        self._settle()
        self._pacer.pause(0.6)

    def _choose(
        self,
        choice: re.Pattern[str],
        stage: Stage,
    ) -> bool:
        """Choose which comments the reel shows, as `stage`, and say whether that was offered."""

        sorting = self._visible("button", SORTING)

        if sorting is None:
            return False

        sorting.click()
        self._pacer.pause(0.5)
        option = self._visible("menuitem", choice)

        if option is None:
            self._page.keyboard.press("Escape")
            return False

        self._stage = stage
        option.click()
        self._settle()
        self._pacer.pause(0.5)

        return True

    def _open_folds(self) -> None:
        """Open every folded comment and reply shown, giving up on any that bring nothing."""

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

    def expand(
        self,
        comment_count: int | None = None,
    ) -> Expansion:
        """Open every comment, then every hidden one, paced, and return what was sent meanwhile.

        All comments are shown and every fold opened; then the comments hidden
        by the person, or by their keyword filter, are shown and opened too.
        When Facebook offers no choice of all comments, what it shows once the
        comments are open is what was shown.
        """

        start = len(self._recorder.records)
        self._stage = "opening"
        self.open_comments(comment_count)
        chosen = self._choose(ALL_COMMENTS, "shown")

        if not chosen:
            self._stage = "shown"

        self._open_folds()

        if self._choose(HIDDEN_BY_YOU, "hidden"):
            self._open_folds()

        staged = list(zip(self._stages[start:], self._recorder.records[start:], strict=True))

        def sent_in(*stages: Stage) -> Records:
            return [record for stage, record in staged if stage in stages]

        if not chosen:
            return Expansion(shown=sent_in("visit", "opening", "shown"), hidden=sent_in("hidden"))

        return Expansion(
            opening=sent_in("visit", "opening"),
            shown=sent_in("shown"),
            hidden=sent_in("hidden"),
        )

    def button_names(self) -> list[str]:
        """Return the name of every visible button on the open page, as a screen reader hears it.

        The name is a button's `aria-label`, or else its text. This is how the
        names `EXPANDERS`, `SORTING` and `OPEN_COMMENTS` look for are found on
        Facebook as it is today.
        """

        names = self._page.eval_on_selector_all(
            "[role=button], button",
            """elements => elements
                .filter(element => element.offsetParent !== null)
                .map(element => (element.getAttribute('aria-label') || element.innerText || '')
                    .trim().replace(/\\s+/g, ' '))
                .filter(name => name)""",
        )

        return [str(name) for name in names]

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
def facebook_browser(
    settings: BrowserSettings | None = None,
    pacer: Pacer | None = None,
) -> Iterator[PlaywrightBrowser]:
    """Open Elder Sign's browser, signed in to Facebook, for looking at a page as it is.

    Raises:
        BrowserError: The browser cannot be opened, or is not signed in to Facebook.
    """

    with open_browser(settings) as context:
        if signed_in_account(context) is None:
            raise BrowserError(NOT_SIGNED_IN)

        yield PlaywrightBrowser(context, pacer or Pacer())


@contextmanager
def facebook(
    settings: BrowserSettings | None = None,
    pacer: Pacer | None = None,
    *,
    keep_capture: Path | None = None,
) -> Iterator[FacebookPlatform]:
    """Open Facebook in Elder Sign's browser, as the account it is signed in to.

    With `keep_capture`, everything Facebook sent is kept there too, as
    `elder-sign capture` keeps it, readable by its owner alone, so a run that
    reads less than it should can be looked into.

    Raises:
        BrowserError: The browser cannot be opened, or is not signed in to Facebook.
    """

    with open_browser(settings) as context:
        account = signed_in_account(context)

        if account is None:
            raise BrowserError(NOT_SIGNED_IN)

        browser = PlaywrightBrowser(context, pacer or Pacer())

        try:
            yield FacebookPlatform(browser, account)
        finally:
            if keep_capture is not None:
                kept = Recorder()
                kept.records = list(browser.records)
                kept.write(keep_capture)
