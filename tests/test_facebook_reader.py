"""Tests proving the Facebook adapter reads the person's own reels and nothing else.

The adapter is run on a browser that replays the canary capture, so every
decision it makes is proven without Facebook. A real browser is then run on
a page made to behave as Facebook's does, to prove it opens what is folded.
"""

import copy
import io
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from elder_sign.browser import BrowserSettings, Pacer, open_browser
from elder_sign.platforms import Platform, Post
from elder_sign.platforms.facebook.parse import comments_in, objects, post_id_of, posts_in
from elder_sign.platforms.facebook.reader import (
    EXPANDERS,
    HIDDEN_BY_YOU,
    OPEN_COMMENTS,
    REEL_URL,
    REELS_TAB,
    SORTING,
    ActionError,
    Expansion,
    FacebookPlatform,
    PlaywrightBrowser,
    fetchable,
    opens_comments,
)
from elder_sign.shared.store import Fingerprint
from tests.test_facebook_parse import canary

NOW = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)


def canary_reel() -> str:
    """Return the canary reel's ID, as its page was opened."""

    reel_id: str = canary()[0]["url"].rstrip("/").rsplit("/", 1)[1]

    return reel_id


class ReplayingBrowser:
    """A browser that replays recorded pages, and records what it was asked to open."""

    def __init__(
        self,
        pages: dict[str, list[dict[str, Any]]],
        links: list[str],
        expanded: Expansion | None = None,
        downloads: dict[str, bytes] | None = None,
    ) -> None:
        self._pages = pages
        self._links = links
        self._expanded = expanded or Expansion()
        self._downloads = downloads or {}
        self.downloaded: list[str] = []
        self.shown: list[str] = []
        self.acted: list[tuple[str, str, bool]] = []
        self.missing: set[str] = set()
        self.visited: list[str] = []
        self.expansions = 0
        self.counts: list[int | None] = []
        self.listed: list[str] = []

    def visit(self, url: str) -> list[dict[str, Any]]:
        self.visited.append(url)
        return list(self._pages.get(url, []))

    def expand(self, comment_count: int | None = None) -> Expansion:
        self.expansions += 1
        self.counts.append(comment_count)
        return self._expanded

    def reel_links(self, url: str) -> list[str]:
        self.listed.append(url)
        return list(self._links)

    def download(self, url: str) -> bytes | None:
        self.downloaded.append(url)
        return self._downloads.get(url)

    def show(self, view: str, comment_count: int | None = None) -> None:
        self.shown.append(view)

    def act(self, comment_id: str, action: str, *, probe: bool = False) -> tuple[str, ...]:
        self.acted.append((comment_id, action, probe))

        if comment_id in self.missing:
            raise ActionError("the comment is not on the page")

        return ("Delete", "Hide comment", "Report comment")


def gif() -> bytes:
    """Return a two-frame GIF, red then blue."""

    out = io.BytesIO()
    red = Image.new("RGB", (40, 30), (255, 0, 0))
    blue = Image.new("RGB", (40, 30), (0, 0, 255))
    red.save(out, format="GIF", save_all=True, append_images=[blue], duration=100, loop=0)

    return out.getvalue()


def old_reel_page(
    reel_id: str,
    created: datetime,
) -> list[dict[str, Any]]:
    """Return a reel page that says only when the reel was published."""

    post = {"post_id": f"post-{reel_id}", "url": REEL_URL.format(reel_id=reel_id)}
    timed = {"post_id": f"post-{reel_id}", "creation_time": int(created.timestamp())}

    return [{"kind": "document", "payloads": [{"data": post}, {"data": timed}]}]


def recounted(
    records: list[dict[str, Any]],
    comment_count: int,
) -> list[dict[str, Any]]:
    """Return a page whose reel Facebook counts this many comments on, serving the same ones."""

    page = copy.deepcopy(records)

    for record in page:
        for value in objects(record.get("payloads", [])):
            if "total_comment_count" in value:
                value["total_comment_count"] = comment_count

    return page


def notice(
    text: str,
) -> dict[str, Any]:
    """Return a response that holds one notice Facebook gives of comments it filtered out."""

    return {"kind": "graphql", "payloads": [{"data": {"filtering_footer_string": text}}]}


def platform_on(
    browser: ReplayingBrowser,
) -> FacebookPlatform:
    """Return the adapter on a replaying browser, as the canary's account, at the canary's time."""

    return FacebookPlatform(browser, "382532529", now=lambda: NOW)


# Listing recent reels


def test_the_adapter_is_a_platform() -> None:
    assert isinstance(platform_on(ReplayingBrowser({}, [])), Platform)


def test_the_adapter_says_which_account_it_is_signed_in_as() -> None:
    assert platform_on(ReplayingBrowser({}, [])).account_id == "382532529"


def test_recent_reels_are_listed_from_the_persons_own_reels_tab() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [f"/reel/{reel}/"])

    (post,) = platform_on(browser).recent_posts(30)

    assert browser.listed == [REELS_TAB.format(account="382532529")]
    assert post.post_id == reel
    assert post.url == REEL_URL.format(reel_id=reel)
    assert post.published_at == datetime(2026, 11, 15, 4, 26, 23, tzinfo=UTC)


def test_a_reel_older_than_the_window_is_left_out_and_a_few_end_the_listing() -> None:
    reel = canary_reel()
    pages = {REEL_URL.format(reel_id=reel): canary()}
    links = [f"https://www.facebook.com/reel/{reel}/"]
    published = [
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
        datetime(2026, 9, 3, tzinfo=UTC),
        datetime(2026, 9, 4, tzinfo=UTC),
        datetime(2026, 9, 5, tzinfo=UTC),
    ]

    for index, created in enumerate(published):
        old = f"9000{index}"
        pages[REEL_URL.format(reel_id=old)] = old_reel_page(old, created)
        links.append(f"/reel/{old}/?s=x")

    browser = ReplayingBrowser(pages, links)

    posts = platform_on(browser).recent_posts(30)

    assert [post.post_id for post in posts] == [reel, "90001"]
    assert len(browser.visited) == 5


def test_a_link_seen_twice_is_one_reel() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser(
        {REEL_URL.format(reel_id=reel): canary()},
        [f"/reel/{reel}/", f"https://www.facebook.com/reel/{reel}/?t=9", "/watch/123"],
    )

    assert len(platform_on(browser).recent_posts(30)) == 1
    assert len(browser.visited) == 1


# Fingerprints


def test_a_fingerprint_is_the_pages_count_and_newest_comment_from_the_listings_visit() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [f"/reel/{reel}/"])
    platform = platform_on(browser)
    (post,) = platform.recent_posts(30)
    newest = max(comments_in(canary()), key=lambda comment: str(comment["created_at"]))

    assert platform.fingerprint(post) == Fingerprint(5, newest["id"])
    assert len(browser.visited) == 1


def test_a_second_fingerprint_is_read_afresh() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [f"/reel/{reel}/"])
    platform = platform_on(browser)
    (post,) = platform.recent_posts(30)

    platform.fingerprint(post)
    platform.fingerprint(post)

    assert len(browser.visited) == 2


def test_a_reel_whose_page_holds_no_count_counts_the_comments_it_loads() -> None:
    reel = canary_reel()
    without_count = [record for record in canary() if record["kind"] != "document"]
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): without_count}, [])

    fingerprint = platform_on(browser).fingerprint(
        Post("facebook", reel, REEL_URL.format(reel_id=reel))
    )

    assert fingerprint.comment_count == 5


def test_comments_facebook_counts_but_never_serves_leave_the_fingerprint_steady() -> None:
    reel = canary_reel()
    page = recounted(canary(), 12)
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): page}, [])
    platform = platform_on(browser)
    post = Post("facebook", reel, REEL_URL.format(reel_id=reel))

    first = platform.fingerprint(post)
    second = platform.fingerprint(post)

    assert len(comments_in(page)) == 5
    assert first.comment_count == 12
    assert first == second


# Scans


def test_a_scan_opens_the_reel_expands_it_and_keeps_only_its_own_comments() -> None:
    reel = canary_reel()
    own_post = next(
        details.post_id for details in posts_in(canary()).values() if details.reel_id == reel
    )
    someone_elses = {
        "kind": "graphql",
        "payloads": [
            {
                "data": {
                    "node": {
                        "__typename": "Comment",
                        "id": "Y29tbWVudDoxXzI=",
                        "author": {"id": "7", "name": "Xx"},
                        "created_time": 1794716999,
                        "body": {"text": "xx"},
                    }
                }
            }
        ],
    }
    browser = ReplayingBrowser(
        {REEL_URL.format(reel_id=reel): canary()}, [], expanded=Expansion(shown=[someone_elses])
    )
    platform = platform_on(browser)

    scan = platform.scan(Post("facebook", reel, REEL_URL.format(reel_id=reel)))

    assert browser.expansions == 1
    assert browser.counts == [5]
    assert scan.get("comment_count") == 5
    assert platform.notices == []
    assert scan["platform"] == "facebook"
    assert scan["post_id"] == reel
    assert len(scan["comments"]) == 5
    assert {post_id_of(comment["id"]) for comment in scan["comments"]} == {own_post}


def test_a_scan_says_what_the_reel_says_and_whose_reel_it_is() -> None:
    reel = canary_reel()
    own_post = next(
        details.post_id for details in posts_in(canary()).values() if details.reel_id == reel
    )
    caption = {
        "kind": "graphql",
        "payloads": [{"data": {"post_id": own_post, "message": {"text": "First date 💜"}}}],
    }
    browser = ReplayingBrowser(
        {REEL_URL.format(reel_id=reel): canary()}, [], expanded=Expansion(opening=[caption])
    )

    scan = platform_on(browser).scan(Post("facebook", reel, REEL_URL.format(reel_id=reel)))

    assert scan.get("caption") == "First date 💜"
    assert scan.get("owner_id") == "382532529"


def test_only_notices_given_while_all_comments_are_shown_are_reported() -> None:
    reel = canary_reel()
    preloaded = notice("Most relevant is selected, so some comments may have been filtered out.")
    page = [*canary(), preloaded]
    expansion = Expansion(
        opening=[notice("Most Relevant is selected, so some replies may have been filtered out.")],
        shown=[notice("Some comments were filtered as spam.")],
        hidden=[notice("Hidden by you is selected, so other comments have been filtered out.")],
    )
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): page}, [], expanded=expansion)
    platform = platform_on(browser)

    platform.scan(Post("facebook", reel, REEL_URL.format(reel_id=reel)))

    assert platform.notices == ["Some comments were filtered as spam."]


def test_a_scan_reads_comments_from_every_part_of_its_expansion() -> None:
    reel = canary_reel()
    (first, *rest) = canary()
    browser = ReplayingBrowser(
        {REEL_URL.format(reel_id=reel): [first]},
        [],
        expanded=Expansion(opening=rest[:3], shown=rest[3:6], hidden=rest[6:]),
    )

    scan = platform_on(browser).scan(Post("facebook", reel, REEL_URL.format(reel_id=reel)))

    assert len(scan["comments"]) == len(comments_in(canary()))


# Acting


def test_acting_shows_all_comments_then_the_hidden_and_reports_each_action() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [])
    browser.missing = {"gone"}
    post = Post("facebook", reel, REEL_URL.format(reel_id=reel))

    results = platform_on(browser).act(
        post, [("c1", "delete"), ("h1", "unhide"), ("gone", "hide")], {"h1"}
    )

    assert browser.shown == ["all", "hidden"]
    assert browser.acted == [
        ("c1", "delete", False),
        ("gone", "hide", False),
        ("h1", "unhide", False),
    ]
    assert [(result.comment_id, result.ok) for result in results] == [
        ("c1", True),
        ("gone", False),
        ("h1", True),
    ]
    assert results[1].detail == "the comment is not on the page"


def test_a_probe_is_passed_through_and_a_view_with_nothing_to_do_is_not_shown() -> None:
    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [])

    platform_on(browser).act(
        Post("facebook", reel, REEL_URL.format(reel_id=reel)), [("c1", "hide")], set(), probe=True
    )

    assert browser.shown == ["all"]
    assert browser.acted == [("c1", "hide", True)]


# Media

GIPHY = "https://media2.giphy.com/media/v1.Y2lkPTc5/abc/giphy.gif"


def test_a_gif_is_fetched_through_the_browser_and_judged_by_its_first_frame() -> None:
    browser = ReplayingBrowser({}, [], downloads={GIPHY: gif()})

    still = platform_on(browser).fetch_media({"kind": "gif", "url": GIPHY})

    assert browser.downloaded == [GIPHY]
    assert still is not None
    assert still.media_type == "image/png"
    assert Image.open(io.BytesIO(still.content)).format == "PNG"


@pytest.mark.parametrize(
    "url",
    [
        "https://scontent-lga3-1.xx.fbcdn.net/v/t39.1997-6/sticker.png?x=1",
        "https://media.giphy.com/media/abc/giphy.gif",
        "https://giphy.com/abc.gif",
        "https://media.tenor.com/abc/tenor.gif",
        "https://lookaside.facebook.com/image?x=1",
    ],
)
def test_media_is_fetched_from_facebooks_cdn_and_the_gif_services_it_embeds(
    url: str,
) -> None:
    assert fetchable(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://media.giphy.com/media/abc/giphy.gif",
        "https://evil.example/giphy.com/abc.gif",
        "https://giphy.com.evil.example/abc.gif",
        "https://notgiphy.com/abc.gif",
        "file:///etc/passwd",
        "https://127.0.0.1/abc.gif",
    ],
)
def test_media_anywhere_else_is_never_fetched(
    url: str,
) -> None:
    browser = ReplayingBrowser({}, [], downloads={url: gif()})

    assert not fetchable(url)
    assert platform_on(browser).fetch_media({"kind": "gif", "url": url}) is None
    assert browser.downloaded == []


def test_media_without_a_url_is_not_fetched() -> None:
    browser = ReplayingBrowser({}, [])

    assert platform_on(browser).fetch_media({"kind": "sticker"}) is None
    assert platform_on(browser).fetch_media({"kind": "gif", "url": None}) is None
    assert browser.downloaded == []


def test_media_that_cannot_be_fetched_or_is_not_an_image_is_none() -> None:
    other = "https://media.giphy.com/media/abc/giphy.mp4"
    browser = ReplayingBrowser({}, [], downloads={other: b"\x00\x00\x00\x18ftypmp42"})
    platform = platform_on(browser)

    assert platform.fetch_media({"kind": "gif", "url": GIPHY}) is None
    assert platform.fetch_media({"kind": "gif", "url": other}) is None


# A real browser, on a page that behaves as Facebook's does


PAGE = """<html><body>
<a href="/reel/111/">a</a> <a href="/reel/222/">b</a>
<div id="more"></div>
<div role="button" aria-label="Comment, 192 comments" id="next">💬</div>
<div role="button" aria-label="Comment, 5 comments" id="open">💬</div>
<div id="pane" hidden>
<button id="sort">Most relevant</button>
<div id="menu" hidden>
<div role="menuitem" id="all">All comments Show all comments, including potential spam.</div>
<div role="menuitem" id="mine">Hidden by You Show your hidden comments in their original context
from the post. Only you can see this.</div>
</div>
<div id="hiddenpane" hidden><div role="button" id="hiddenreply">View 1 hidden reply</div></div>
<div role="button" id="replies">View 1 reply</div>
<div role="button" id="comments">View more comments</div>
<div role="button" id="dead">View 2 replies</div>
</div>
<script>
const ask = name => fetch('/api/graphql/', {method: 'POST', headers:
  {'Content-Type': 'application/x-www-form-urlencoded'},
  body: 'fb_api_req_friendly_name=' + name + '&doc_id=1&variables=%7B%7D'});
document.getElementById('next').onclick = () => { ask('NextReelCommentsQuery'); };
document.getElementById('open').onclick = () => { ask('CommentsRightRailQuery');
  document.getElementById('pane').hidden = false; };
document.getElementById('sort').onclick = () => { document.getElementById('menu').hidden = false; };
document.getElementById('all').onclick = () => { ask('AllCommentsQuery');
  document.getElementById('menu').hidden = true; };
document.getElementById('mine').onclick = () => { ask('HiddenByYouQuery');
  document.getElementById('menu').hidden = true;
  document.getElementById('hiddenpane').hidden = false; };
document.getElementById('hiddenreply').onclick = e => { ask('HiddenRepliesQuery');
  e.target.remove(); };
document.getElementById('replies').onclick = e => { ask('Depth1CommentsListPaginationQuery');
  e.target.remove(); };
document.getElementById('comments').onclick = e => { ask('CommentsListPaginationQuery');
  e.target.remove(); };
let scrolled = false;
window.onscroll = () => { if (!scrolled) { scrolled = true;
  document.getElementById('more').innerHTML = '<a href="/reel/333/">c</a>'; } };
document.body.style.height = '5000px';
</script></body></html>"""


class Facebookish(BaseHTTPRequestHandler):
    """Serve the page, and answer its GraphQL as Facebook does."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/media.gif":
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.end_headers()
            self.wfile.write(gif())
            return

        if self.path == "/gone.gif":
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'for (;;);{"data": {}}')


@contextmanager
def served() -> Iterator[str]:
    """Serve the page on a local port for one test."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Facebookish)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ELDER_SIGN_BROWSER_TESTS") != "1",
    reason="opens a real browser; set ELDER_SIGN_BROWSER_TESTS=1",
)
def test_a_real_browser_opens_the_comments_shows_all_opens_every_fold_and_finds_every_reel(
    tmp_path: Path,
) -> None:
    settings = BrowserSettings(profile=tmp_path / "profile", channel=None, headless=True)
    pacer = Pacer(0.0, 0.0, sleep=lambda seconds: None)

    with served() as root, open_browser(settings) as context:
        browser = PlaywrightBrowser(context, pacer, settle_milliseconds=1500)

        links = browser.reel_links(root + "/profile")
        visited = browser.visit(root + "/reel/111/")
        closed = browser.button_names()
        expanded = browser.expand(5)

    def named(records: list[dict[str, Any]]) -> list[str]:
        return [str(record["friendly_name"]) for record in records if record["kind"] == "graphql"]

    names = named(expanded.records)

    assert [link.rsplit("/reel/", 1)[1] for link in links] == ["111/", "222/", "333/"]
    assert closed == ["Comment, 192 comments", "Comment, 5 comments"]
    assert "NextReelCommentsQuery" not in names
    assert [record["kind"] for record in visited] == ["document"]
    assert names[:2] == ["CommentsRightRailQuery", "AllCommentsQuery"]
    assert sorted(names[2:4]) == [
        "CommentsListPaginationQuery",
        "Depth1CommentsListPaginationQuery",
    ]
    assert names[4:] == ["HiddenByYouQuery", "HiddenRepliesQuery"]
    assert named(expanded.opening) == ["CommentsRightRailQuery"]
    assert named(expanded.shown)[0] == "AllCommentsQuery"
    assert named(expanded.hidden) == ["HiddenByYouQuery", "HiddenRepliesQuery"]
    assert json.dumps(expanded.records).count("Depth1CommentsListPaginationQuery") == 1


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ELDER_SIGN_BROWSER_TESTS") != "1",
    reason="opens a real browser; set ELDER_SIGN_BROWSER_TESTS=1",
)
def test_a_real_browser_downloads_media_and_refuses_what_is_missing_or_too_heavy(
    tmp_path: Path,
) -> None:
    settings = BrowserSettings(profile=tmp_path / "profile", channel=None, headless=True)
    pacer = Pacer(0.0, 0.0, sleep=lambda seconds: None)

    with served() as root, open_browser(settings) as context:
        browser = PlaywrightBrowser(context, pacer, settle_milliseconds=1500)

        fetched = browser.download(root + "/media.gif")
        missing = browser.download(root + "/gone.gif")
        heavy = browser.download(root + "/media.gif", max_bytes=10)
        unreachable = browser.download("http://127.0.0.1:9/nothing.gif")

    assert fetched == gif()
    assert missing is None
    assert heavy is None
    assert unreachable is None


# The commands


def replay(
    monkeypatch: pytest.MonkeyPatch,
    page: list[dict[str, Any]],
    expansion: Expansion | None = None,
    downloads: dict[str, bytes] | None = None,
) -> ReplayingBrowser:
    """Stand a browser replaying one reel's page in for Facebook, for the commands."""

    import elder_sign.platforms.facebook.reader as reader

    reel = canary_reel()
    browser = ReplayingBrowser(
        {REEL_URL.format(reel_id=reel): page},
        [f"/reel/{reel}/"],
        expanded=expansion,
        downloads=downloads,
    )

    @contextmanager
    def facebook(
        settings: Any = None, pacer: Any = None, **options: Any
    ) -> Iterator[FacebookPlatform]:
        yield platform_on(browser)

    monkeypatch.setattr(reader, "facebook", facebook)

    return browser


@pytest.fixture
def replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> ReplayingBrowser:
    """Stand the replaying canary in for Facebook, for the commands."""

    return replay(monkeypatch, canary())


def test_the_read_command_lists_recent_reels_and_their_counts(
    replayed: ReplayingBrowser,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    assert main(["read", "facebook", "--hidden"]) == 0

    printed = capsys.readouterr().out

    assert f"{canary_reel()}  published 2026-11-15  5 comments" in printed
    assert "1 reels from the last 30 days." in printed


def test_the_scan_command_keeps_what_it_read_where_only_its_owner_can_read_it(
    replayed: ReplayingBrowser,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from elder_sign.cli import main

    out = tmp_path / "scan.json"

    assert (
        main(["scan", "facebook", REEL_URL.format(reel_id=canary_reel()), "--out", str(out)]) == 0
    )

    printed = capsys.readouterr().out
    scan = json.loads(out.read_text())

    assert "Read 5 comments" in printed
    assert "5 visible + 0 hidden = 5; Facebook counts 5." in printed
    assert "Everything Facebook sent" not in printed
    assert "4 top-level, 1 replies, 1 with a GIF" in printed
    assert len(scan["comments"]) == 5
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_the_scan_command_says_how_many_comments_facebook_counts_but_never_serves(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from elder_sign.cli import main

    replay(
        monkeypatch,
        recounted(canary(), 12),
        Expansion(
            opening=[notice("Most relevant is selected, so some comments may have been filtered.")],
            shown=[notice("Some comments were filtered as spam.")],
        ),
    )
    url = REEL_URL.format(reel_id=canary_reel())

    assert main(["scan", "facebook", url, "--out", str(tmp_path / "scan.json")]) == 0

    printed = capsys.readouterr().out

    assert (
        "5 visible + 0 hidden = 5; Facebook counts 12: "
        "7 counted but not served (deleted, orphaned or from blocked accounts)."
    ) in printed
    assert "Facebook says: Some comments were filtered as spam." in printed
    assert "Most relevant" not in printed


def test_the_scan_command_fetches_media_when_asked_and_keeps_it_privately(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from elder_sign.cli import main

    (url,) = [
        str(item["url"])
        for comment in comments_in(canary())
        for item in comment.get("media", [])
        if item.get("url")
    ]
    browser = replay(monkeypatch, canary(), downloads={url: gif()})
    out = tmp_path / "scan.json"
    reel = REEL_URL.format(reel_id=canary_reel())

    assert main(["scan", "facebook", reel, "--out", str(out), "--media"]) == 0

    printed = capsys.readouterr().out
    kept = sorted((tmp_path / "scan-media").iterdir())

    assert browser.downloaded == [url]
    assert "Fetched 1 of 1 GIFs, stickers and images as stills" in printed
    assert [path.suffix for path in kept] == [".png"]
    assert oct(kept[0].stat().st_mode & 0o777) == "0o600"


def test_the_scan_command_fetches_no_media_unless_asked(
    replayed: ReplayingBrowser,
    tmp_path: Path,
) -> None:
    from elder_sign.cli import main

    reel = REEL_URL.format(reel_id=canary_reel())

    assert main(["scan", "facebook", reel, "--out", str(tmp_path / "scan.json")]) == 0
    assert replayed.downloaded == []
    assert not (tmp_path / "scan-media").exists()


def test_the_scan_command_refuses_a_link_that_is_not_a_reel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    assert main(["scan", "facebook", "https://www.facebook.com/watch/1"]) == 1
    assert "not a link to a reel" in capsys.readouterr().err


# Button names, as Facebook gives them


@pytest.mark.parametrize(
    "name",
    [
        "Comment, 5 comments",
        "Comment, 1 comment",
        "Comment, 7.5K comments",
        "Comment",
        "12 comments",
    ],
)
def test_the_button_that_opens_comments_is_known_by_its_name(
    name: str,
) -> None:
    assert OPEN_COMMENTS.match(name)


@pytest.mark.parametrize("name", ["Like, 7.5K reactions", "Share, 0 shares", "Comment as Eris"])
def test_other_buttons_are_not_taken_for_it(
    name: str,
) -> None:
    assert not OPEN_COMMENTS.match(name)


def test_a_reels_own_button_is_the_one_named_with_its_count() -> None:
    assert opens_comments(5).match("Comment, 5 comments")
    assert not opens_comments(5).match("Comment, 192 comments")
    assert opens_comments(1).match("Comment, 1 comment")
    assert opens_comments(1234).match("Comment, 1,234 comments")


@pytest.mark.parametrize(
    "name",
    [
        "View hidden comments",
        "See 3 filtered comments",
        "Show hidden replies",
        "View 1 hidden reply",
    ],
)
def test_buttons_that_show_hidden_or_filtered_comments_are_opened_too(
    name: str,
) -> None:
    assert any(pattern.match(name) for pattern in EXPANDERS)


def test_the_choice_of_hidden_comments_is_known_by_its_name() -> None:
    assert HIDDEN_BY_YOU.match(
        "Hidden by You Show your hidden comments in their original context from the post."
    )
    assert not HIDDEN_BY_YOU.match("All comments Show all comments, including potential spam.")
    assert SORTING.match("Hidden by You")
