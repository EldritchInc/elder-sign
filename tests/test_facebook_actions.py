"""Tests proving Elder Sign acts on exactly the comment it means, on a page built like Facebook's.

The page names everything as Facebook does today: Hide and Unhide buttons
under each comment, a "…" menu with Delete, Hide comment and Report comment,
and a "Delete Comment?" dialog asking No or Delete. Each comment carries its
own permalink, `comment_id=` or `reply_comment_id=`, as on Facebook, and a
comment numbered as the start of another's number is there to be mistaken.
"""

import base64
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from elder_sign.browser import BrowserSettings, Pacer, open_browser
from elder_sign.platforms.facebook.reader import HIDDEN_INSTEAD, ActionError, PlaywrightBrowser

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


ENCODED_109 = base64.b64encode(b"comment:900_109").decode().replace("=", "%3D")

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
<div role="article" id="c106" aria-label="Comment">
<span class="text">not hers</span> <a href="/reel/1/?comment_id=106">5h</a>
<div role="button">Reply</div>
<div role="button" class="menu" aria-label="Delete, hide or report this">…</div>
</div>
<div role="article" id="c105" aria-label="Comment">This comment was hidden.
<a href="/reel/1/?comment_id=105&reply_comment_id=305">6h</a>
<div role="button" class="unhide">Unhide</div></div>
<div role="article" id="c107" aria-label="Comment">This comment was hidden.
<div role="button" class="unhide">Unhide</div></div>
<div role="article" id="c103" aria-label="Comment" data-hidden="1">
<span class="text">hidden one</span> <a href="/reel/1/?comment_id=103">3h</a>
<div role="button" class="unhide">Unhide</div>
<div role="button" class="menu" aria-label="Edit or delete this">…</div>
</div>
<div role="article" id="c108" aria-label="Comment">
<span class="text">tagging her</span> <a href="/reel/1/?comment_id=108">7h</a>
<div role="button" class="tagmenu" aria-label="Delete, hide or report this">…</div>
</div>
<div role="article" id="c111" aria-label="Comment" data-hidden="1">This comment was hidden.
<a href="/reel/1/?comment_id=111">10h</a>
<div role="button" class="unhide">Unhide</div>
<div role="button" class="menu" aria-label="Delete, unhide or report this">…</div>
</div>
<div role="article" id="c109" aria-label="Comment">This comment was hidden.
<a href="/reel/1/?comment_id={ENCODED_109}">8h</a>
<div role="button" class="unhide">Unhide</div></div>
<div role="article" id="c112" aria-label="Comment">
<span class="text">becomes a placeholder</span> <a href="/reel/1/?comment_id=112">11h</a>
<div role="button" class="vanish">Hide</div>
</div>
<div role="menu" id="menu" hidden>
<div role="menuitem" id="delete">Delete</div>
<div role="menuitem" id="hidecomment">Hide comment</div>
<div role="menuitem">Report comment</div>
</div>
<div role="article" id="c110" aria-label="Comment">
<span class="text">a real reel comment</span> <a href="/reel/1/?comment_id=110">9h</a>
<div role="button" class="realmenu" aria-label="Delete, hide or report this">…</div>
</div>
<div class="prerendered">
<div role="menuitem">Remove tag</div><div role="menuitem">Block Someone</div>
</div>
<div role="menu" id="realmenu" hidden>
<div tabindex="0" id="realdelete">Delete</div>
<div role="button" id="realhide">Hide comment</div>
<div role="button">Report comment</div>
</div>
<div role="menu" id="tagmenu" hidden>
<div role="menuitem">Remove tag</div>
<div role="menuitem" id="taghide">Hide comment</div>
<div role="menuitem">Block Someone You won't be able to see or contact each other.</div>
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
document.querySelectorAll('.vanish').forEach(button => button.onclick = event => {{
  event.stopPropagation(); const article = button.closest('[role=article]');
  article.dataset.hidden = '1';
  article.innerHTML = 'This comment was hidden. <div role="button">Unhide</div>'; }});
const realmenu = document.getElementById('realmenu');
document.querySelectorAll('.realmenu').forEach(button => button.onclick = event => {{
  event.stopPropagation(); target = button.closest('[role=article]'); realmenu.hidden = false; }});
document.getElementById('realdelete').onclick = () => {{ realmenu.hidden = true;
  dialog.hidden = false; }};
document.addEventListener('keydown', event => {{
  if (event.key === 'Escape') realmenu.hidden = true; }});
const tagmenu = document.getElementById('tagmenu');
document.querySelectorAll('.tagmenu').forEach(button => button.onclick = event => {{
  event.stopPropagation(); target = button.closest('[role=article]'); tagmenu.hidden = false; }});
document.getElementById('taghide').onclick = () => {{
  target.dataset.hidden = '1'; tagmenu.hidden = true; }};
document.addEventListener('keydown', event => {{
  if (event.key === 'Escape') tagmenu.hidden = true; }});
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
        "c106": "0",
        "c105": "0",
        "c107": "0",
        "c108": "0",
        "c109": "0",
        "c110": "0",
        "c111": "1",
        "c112": "0",
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
    assert deleting == ("Delete [menuitem]", "Hide comment [menuitem]", "Report comment [menuitem]")
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
    assert re.search(r"\d+ reading 'This comment was hidden'", str(raised.value))
    assert "linked as: comment_id=105+reply_comment_id=305" in str(raised.value)


def test_another_persons_comment_is_deleted_through_its_own_menu_name(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        menu = browser.act(comment_id("106"), "delete")
        after = state(page)

    assert menu == ("Delete", "Hide comment", "Report comment")
    assert "c106" not in after


def test_where_facebook_offers_no_delete_the_comment_is_hidden_instead(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        probed = browser.act(comment_id("108"), "delete", probe=True)
        unchanged = state(page)["c108"]
        menu = browser.act(comment_id("108"), "delete")
        after = state(page)

    assert probed[-1] == HIDDEN_INSTEAD
    assert "Remove tag [menuitem]" in probed
    assert unchanged == "0"
    assert menu[-1] == HIDDEN_INSTEAD
    assert after["c108"] == "1"


def test_a_hidden_comment_linked_by_its_whole_id_is_found(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        browser.act(comment_id("109"), "unhide")
        after = state(page)

    assert after["c109"] == "0"


def test_the_menu_that_opened_is_read_and_not_stray_items_elsewhere_on_the_page(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        probed = browser.act(comment_id("110"), "delete", probe=True)
        menu = browser.act(comment_id("110"), "delete")
        after = state(page)

    assert probed == ("Delete [clickable]", "Hide comment [button]", "Report comment [button]")
    assert menu == ("Delete", "Hide comment", "Report comment")
    assert "c110" not in after


def test_a_hidden_comment_is_deleted_through_its_own_menu_name(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        browser.act(comment_id("111"), "delete")
        after = state(page)

    assert "c111" not in after


@pytest.mark.parametrize(
    "name",
    [
        "Edit or delete this",
        "Delete, hide or report this",
        "Delete, unhide or report this",
        "Hide or report this",
        "Edit, delete, hide or report this",
    ],
)
def test_every_name_facebook_gives_a_comments_menu_is_known(
    name: str,
) -> None:
    from elder_sign.platforms.facebook.reader import COMMENT_MENU

    assert COMMENT_MENU.match(name)


@pytest.mark.parametrize("name", ["Delete", "Hide", "Report this post", "Like", "Reply"])
def test_no_other_button_is_taken_for_a_comments_menu(
    name: str,
) -> None:
    from elder_sign.platforms.facebook.reader import COMMENT_MENU

    assert not COMMENT_MENU.match(name)


def test_hiding_a_comment_facebook_then_swaps_for_a_placeholder_returns_at_once(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):
        page.set_default_timeout(3000)
        menu = browser.act(comment_id("112"), "hide")
        after = state(page)

    assert menu == ("[button] Hide",)
    assert after["c112"] == "1"


def test_whatever_the_browser_fails_at_is_the_comments_failure_never_the_runs(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as (browser, page):

        def breaks(*arguments: Any, **options: Any) -> tuple[str, ...]:
            from playwright.sync_api import Error

            raise Error("Locator.inner_text: Timeout 30000ms exceeded.\nCall log: …")

        browser._act = breaks  # type: ignore[method-assign]

        with pytest.raises(ActionError, match="the browser failed while acting on the comment"):
            browser.act(comment_id("101"), "hide")
