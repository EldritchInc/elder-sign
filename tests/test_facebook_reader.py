"""Tests proving the Facebook adapter reads the person's own reels and nothing else.

The adapter is run on a browser that replays the canary capture, so every
decision it makes is proven without Facebook. A real browser is then run on
a page made to behave as Facebook's does, to prove it opens what is folded.
"""

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

from elder_sign.browser import BrowserSettings, Pacer, open_browser
from elder_sign.platforms import Platform, Post
from elder_sign.platforms.facebook.parse import comments_in, post_id_of, posts_in
from elder_sign.platforms.facebook.reader import (
    REEL_URL,
    REELS_TAB,
    FacebookPlatform,
    PlaywrightBrowser,
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
        expanded: list[dict[str, Any]] | None = None,
    ) -> None:
        self._pages = pages
        self._links = links
        self._expanded = expanded or []
        self.visited: list[str] = []
        self.expansions = 0
        self.listed: list[str] = []

    def visit(self, url: str) -> list[dict[str, Any]]:
        self.visited.append(url)
        return list(self._pages.get(url, []))

    def expand(self) -> list[dict[str, Any]]:
        self.expansions += 1
        return list(self._expanded)

    def reel_links(self, url: str) -> list[str]:
        self.listed.append(url)
        return list(self._links)


def old_reel_page(
    reel_id: str,
    created: datetime,
) -> list[dict[str, Any]]:
    """Return a reel page that says only when the reel was published."""

    post = {"post_id": f"post-{reel_id}", "url": REEL_URL.format(reel_id=reel_id)}
    timed = {"post_id": f"post-{reel_id}", "creation_time": int(created.timestamp())}

    return [{"kind": "document", "payloads": [{"data": post}, {"data": timed}]}]


def platform_on(
    browser: ReplayingBrowser,
) -> FacebookPlatform:
    """Return the adapter on a replaying browser, as the canary's account, at the canary's time."""

    return FacebookPlatform(browser, "382532529", now=lambda: NOW)


# Listing recent reels


def test_the_adapter_is_a_platform() -> None:
    assert isinstance(platform_on(ReplayingBrowser({}, [])), Platform)


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
        {REEL_URL.format(reel_id=reel): canary()}, [], expanded=[someone_elses]
    )
    platform = platform_on(browser)

    scan = platform.scan(Post("facebook", reel, REEL_URL.format(reel_id=reel)))

    assert browser.expansions == 1
    assert scan["platform"] == "facebook"
    assert scan["post_id"] == reel
    assert len(scan["comments"]) == 5
    assert {post_id_of(comment["id"]) for comment in scan["comments"]} == {own_post}


# A real browser, on a page that behaves as Facebook's does


PAGE = """<html><body>
<a href="/reel/111/">a</a> <a href="/reel/222/">b</a>
<div id="more"></div>
<div role="button" aria-label="Comment" id="open">💬</div>
<div id="pane" hidden>
<button id="sort">Most relevant</button>
<div id="menu" hidden><div role="menuitem" id="all">All comments</div></div>
<div role="button" id="replies">View 1 reply</div>
<div role="button" id="comments">View more comments</div>
<div role="button" id="dead">View 2 replies</div>
</div>
<script>
const ask = name => fetch('/api/graphql/', {method: 'POST', headers:
  {'Content-Type': 'application/x-www-form-urlencoded'},
  body: 'fb_api_req_friendly_name=' + name + '&doc_id=1&variables=%7B%7D'});
document.getElementById('open').onclick = () => { ask('CommentsRightRailQuery');
  document.getElementById('pane').hidden = false; };
document.getElementById('sort').onclick = () => { document.getElementById('menu').hidden = false; };
document.getElementById('all').onclick = () => { ask('AllCommentsQuery');
  document.getElementById('menu').hidden = true; };
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
        expanded = browser.expand()

    names = [str(record["friendly_name"]) for record in expanded if record["kind"] == "graphql"]

    assert [link.rsplit("/reel/", 1)[1] for link in links] == ["111/", "222/", "333/"]
    assert [record["kind"] for record in visited] == ["document"]
    assert names[:2] == ["CommentsRightRailQuery", "AllCommentsQuery"]
    assert sorted(names[2:]) == ["CommentsListPaginationQuery", "Depth1CommentsListPaginationQuery"]
    assert json.dumps(expanded).count("Depth1CommentsListPaginationQuery") == 1


# The commands


@pytest.fixture
def replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> ReplayingBrowser:
    """Stand the replaying browser in for Facebook, for the commands."""

    import elder_sign.platforms.facebook.reader as reader

    reel = canary_reel()
    browser = ReplayingBrowser({REEL_URL.format(reel_id=reel): canary()}, [f"/reel/{reel}/"])

    @contextmanager
    def facebook(
        settings: Any = None, pacer: Any = None, **options: Any
    ) -> Iterator[FacebookPlatform]:
        yield platform_on(browser)

    monkeypatch.setattr(reader, "facebook", facebook)

    return browser


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
    assert "Everything Facebook sent" not in printed
    assert "4 top-level, 1 replies, 1 with a GIF" in printed
    assert len(scan["comments"]) == 5
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_the_scan_command_refuses_a_link_that_is_not_a_reel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main

    assert main(["scan", "facebook", "https://www.facebook.com/watch/1"]) == 1
    assert "not a link to a reel" in capsys.readouterr().err
