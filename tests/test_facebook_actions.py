"""Tests proving Elder Sign acts on exactly the comment it means, on a page built like Facebook's.

The page names everything as Facebook does today: Hide and Unhide buttons
under each comment, a "…" menu with Delete, Hide comment and Report comment,
and a "Delete Comment?" dialog asking No or Delete. Each comment carries its
own permalink, `comment_id=` or `reply_comment_id=`, as on Facebook, and a
comment numbered as the start of another's number is there to be mistaken.
"""

import base64
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from elder_sign.browser import BrowserSettings, Pacer, open_browser
from elder_sign.platforms.facebook.reader import ActionError, PlaywrightBrowser

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ELDER_SIGN_BROWSER_TESTS") != "1",
        reason="opens a real browser; set ELDER_SIGN_BROWSER_TESTS=1",
    ),
]


def comment_id(
    number: str,
) -> str:
    """Return a comment's ID as Facebook gives it."""

    return base64.b64encode(f"comment:900_{number}".encode()).decode()


def article(
    number: str,
    text: str,
    href: str,
) -> str:
    """Return one comment as the page shows it."""

    return f"""<div role="article" id="c{number}" aria-label="Comment">
<span class="text">{text}</span> <a href="{href}">1h</a>
<div role="button" class="hide">Hide</div>
<div role="button" class="menu" aria-label="Edit or delete this">…</div>
</div>"""


PAGE = f"""<html><body>
{article("101", "the one", "/reel/1/?comment_id=101&__cft__=x")}
{article("1012", "the one numbered like it", "/reel/1/?comment_id=1012")}
<div role="article" id="c102" aria-label="Comment">
<span class="text">a thread</span> <a href="/reel/1/?comment_id=102">2h</a>
<div role="button" class="hide">Hide</div>
<div role="button" class="menu" aria-label="Edit or delete this">…</div>
{article("202", "a reply", "/reel/1/?comment_id=102&reply_comment_id=202&__tn__=R")}
</div>
<div role="article" id="c104" aria-label="Comment">
<span class="text">someone else's</span> <a href="/reel/1/?comment_id=104">4h</a>
<div role="button">Reply</div><div role="button" aria-label="Some new name">…</div>
</div>
<div role="article" id="c105" aria-label="Comment">This comment was hidden.
<div role="button" class="unhide">Unhide</div></div>
<div role="article" id="c103" aria-label="Comment" data-hidden="1">
<span class="text">hidden one</span> <a href="/reel/1/?comment_id=103">3h</a>
<div role="button" class="unhide">Unhide</div>
<div role="button" class="menu" aria-label="Edit or delete this">…</div>
</div>
<div role="menu" id="menu" hidden>
<div role="menuitem" id="delete">Delete</div>
<div role="menuitem" id="hidecomment">Hide comment</div>
<div role="menuitem">Report comment</div>
</div>
<div role="dialog" id="dialog" hidden>Delete Comment?
<div role="button" id="no">No</div><div role="button" id="yes">Delete</div>
</div>
<script>
let target = null;
const menu = document.getElementById('menu');
const dialog = document.getElementById('dialog');
document.querySelectorAll('.menu').forEach(button => button.onclick = event => {{
  event.stopPropagation(); target = button.closest('[role=article]'); menu.hidden = false; }});
document.querySelectorAll('.hide').forEach(button => button.onclick = event => {{
  event.stopPropagation(); button.closest('[role=article]').dataset.hidden = '1'; }});
document.querySelectorAll('.unhide').forEach(button => button.onclick = event => {{
  event.stopPropagation(); button.closest('[role=article]').dataset.hidden = '0'; }});
document.addEventListener('keydown', event => {{
  if (event.key === 'Escape') menu.hidden = true; }});
document.getElementById('hidecomment').onclick = () => {{
  target.dataset.hidden = '1'; menu.hidden = true; }};
document.getElementById('delete').onclick = () => {{ menu.hidden = true; dialog.hidden = false; }};
document.getElementById('no').onclick = () => {{ dialog.hidden = true; }};
document.getElementById('yes').onclick = () => {{ target.remove(); dialog.hidden = true; }};
</script></body></html>"""


class Facebookish(BaseHTTPRequestHandler):
    """Serve the page."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE.encode())


@contextmanager
def opened(
    tmp_path: Path,
) -> Iterator[tuple[PlaywrightBrowser, Any]]:
    """Open the page in a real browser, unpaced, and yield the browser and its page."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Facebookish)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    settings = BrowserSettings(profile=tmp_path / "profile", channel=None, headless=True)

    try:
        with open_browser(settings) as context:
            browser = PlaywrightBrowser(
                context, Pacer(0.0, 0.0, sleep=lambda seconds: None), settle_milliseconds=500
            )
            browser.visit(f"http://127.0.0.1:{httpd.server_address[1]}/reel/1/")
            yield browser, browser._page
    finally:
        httpd.shutdown()


def state(
    page: Any,
) -> dict[str, str]:
    """Return each comment still on the page, and whether it is hidden."""

    found: dict[str, str] = page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('[role=article]')]"
        ".map(a => [a.id, a.dataset.hidden || '0']))"
    )

    return found


def test_hiding_uses_the_button_under_that_comment_alone(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        menu = browser.act(comment_id("101"), "hide")
        after = state(page)

    assert menu == ("[button] Hide",)
    assert after == {
        "c101": "1",
        "c1012": "0",
        "c102": "0",
        "c202": "0",
        "c104": "0",
        "c105": "0",
        "c103": "1",
    }


def test_a_reply_is_found_by_its_own_number_not_its_threads(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        browser.act(comment_id("202"), "hide")
        after = state(page)

    assert (after["c202"], after["c102"]) == ("1", "0")


def test_deleting_chooses_delete_from_the_menu_and_confirms_it(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        menu = browser.act(comment_id("1012"), "delete")
        after = state(page)

    assert menu == ("Delete", "Hide comment", "Report comment")
    assert "c1012" not in after
    assert "c101" in after


def test_unhiding_uses_the_unhide_button(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        browser.act(comment_id("103"), "unhide")
        after = state(page)

    assert after["c103"] == "0"


def test_a_probe_reads_the_menu_and_clicks_nothing(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        before = state(page)
        deleting = browser.act(comment_id("101"), "delete", probe=True)
        hiding = browser.act(comment_id("202"), "hide", probe=True)
        after = state(page)
        menu_closed = page.evaluate("() => document.getElementById('menu').hidden")

    assert before == after
    assert deleting == ("Delete", "Hide comment", "Report comment")
    assert hiding[0] == "[button] Hide"
    assert menu_closed is True


def test_a_comment_not_on_the_page_is_an_error_saying_so(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, _), pytest.raises(ActionError, match="not on the page"):
        browser.act(comment_id("999"), "delete")


def test_a_menu_button_by_another_name_is_reported_with_every_button_near_the_comment(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, _), pytest.raises(ActionError) as raised:
        browser.act(comment_id("104"), "delete", probe=True)

    assert "menu button was not found" in str(raised.value)
    assert "Reply" in raised.value.menu
    assert "Some new name" in raised.value.menu


def test_a_comment_not_found_says_what_the_page_shows(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, _), pytest.raises(ActionError) as raised:
        browser.act(comment_id("999"), "unhide")

    assert "1 without a permalink" in str(raised.value)
    assert "1 reading 'This comment was hidden'" in str(raised.value)
