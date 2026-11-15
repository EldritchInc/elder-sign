"""Capture what Facebook sends a browser for one reel, to learn the shapes Elder Sign must read.

Facebook's GraphQL responses are undocumented, so Elder Sign's parser is
written from real ones. `elder-sign capture facebook <reel-url>` opens the
reel in Elder Sign's browser, and records, while the person expands every
comment and reply by hand:

- every response to `/api/graphql/`, with the query it answered, by its
  friendly name and document ID, and its variables;
- every page the browser loads, because the first comments arrive inside
  the page itself, as JSON in its `<script>` tags, not by GraphQL.

The capture holds real people's names and words, so it is written only to
`~/.elder-sign/captures/`, readable by its owner alone, and never to the
repository. `elder-sign scrub` makes from it a fixture that is safe to share.

A capture is JSON Lines, one response a line:

    {"kind": "graphql", "url": "...", "status": 200, "friendly_name": "...",
     "doc_id": "...", "variables": {...}, "body": "<the response, as sent>"}
    {"kind": "document", "url": "...", "status": 200, "body": "<the page's HTML>"}
"""

import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs, urlsplit

from elder_sign.browser import BrowserSettings, open_browser

if TYPE_CHECKING:
    from playwright.sync_api import Page, Response

GRAPHQL_PATH: Final = "/api/graphql/"
"""The path every Facebook GraphQL query is sent to."""

PUMP_MILLISECONDS: Final = 250
"""How often the browser is called, while the person expands comments, to deliver responses."""

CAPTURE_MODE: Final = 0o600
"""A capture is readable and writable by its owner alone."""


def captures_path() -> Path:
    """Return where captures are kept: `~/.elder-sign/captures`."""

    return Path.home() / ".elder-sign" / "captures"


def _form(
    post_data: str | None,
) -> dict[str, str]:
    """Return a request's form fields, each its first value."""

    if not post_data:
        return {}

    return {name: values[0] for name, values in parse_qs(post_data).items() if values}


def _variables(
    encoded: str | None,
) -> Any:
    """Return a query's variables as JSON, or as sent when they are not JSON."""

    if encoded is None:
        return None

    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return encoded


class Recorder:
    """Keep the responses a capture is made of, as they arrive."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def observe(
        self,
        *,
        url: str,
        method: str,
        resource_type: str,
        status: int,
        post_data: str | None,
        body: Callable[[], str],
    ) -> bool:
        """Keep one response if a capture keeps its kind, and return whether it did.

        `body` is called only for a response that is kept, so the browser is
        never asked for a body nobody wants.
        """

        path = urlsplit(url).path

        if method == "POST" and path.startswith(GRAPHQL_PATH):
            form = _form(post_data)
            self.records.append(
                {
                    "kind": "graphql",
                    "url": url,
                    "status": status,
                    "friendly_name": form.get("fb_api_req_friendly_name"),
                    "doc_id": form.get("doc_id"),
                    "variables": _variables(form.get("variables")),
                    "body": body(),
                }
            )
            return True

        if resource_type == "document":
            self.records.append({"kind": "document", "url": url, "status": status, "body": body()})
            return True

        return False

    def observe_response(
        self,
        response: "Response",
    ) -> None:
        """Keep one browser response if a capture keeps its kind."""

        request = response.request

        def body() -> str:
            try:
                return response.text()
            except Exception as exc:  # a body the browser lost is noted, never fatal
                return f"<body unavailable: {exc}>"

        self.observe(
            url=response.url,
            method=request.method,
            resource_type=request.resource_type,
            status=response.status,
            post_data=request.post_data,
            body=body,
        )

    def write(
        self,
        path: Path,
    ) -> Path:
        """Write the capture as JSON Lines, readable by its owner alone, and return its path."""

        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, CAPTURE_MODE)

        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for record in self.records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        os.chmod(path, CAPTURE_MODE)

        return path


def default_capture_path(
    now: datetime | None = None,
) -> Path:
    """Return the file a capture made now is written to."""

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")

    return captures_path() / f"facebook-{stamp}.jsonl"


def capture(
    url: str,
    output: Path,
    settings: BrowserSettings | None = None,
    *,
    wait: Callable[[], object] = input,
    show: bool = True,
) -> tuple[Path, int]:
    """Open a reel, record every response until the person says they are done, and write them.

    The browser is shown, so the person can expand every comment and reply
    themselves. Returns where the capture was written and how many responses
    it holds.
    """

    settings = settings or BrowserSettings()
    shown = BrowserSettings(profile=settings.profile, channel=settings.channel, headless=not show)
    recorder = Recorder()

    with open_browser(shown) as context:
        context.on("response", recorder.observe_response)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url)
        _keep_recording(page, wait)

    return recorder.write(output), len(recorder.records)


def _keep_recording(
    page: "Page",
    wait: Callable[[], object],
) -> None:
    """Let the browser keep delivering responses until the person says they are done.

    Playwright hands a response to its listener only while it is being called,
    and a response's body is only there to read while its page is open. So the
    person is waited for on another thread, while this one keeps calling the
    browser, every `PUMP_MILLISECONDS`, until they are done or close the window.
    """

    from playwright.sync_api import Error

    done = threading.Event()

    def waiting() -> None:
        try:
            wait()
        finally:
            done.set()

    threading.Thread(target=waiting, daemon=True).start()

    while not done.is_set():
        try:
            page.wait_for_timeout(PUMP_MILLISECONDS)
        except Error:
            return
