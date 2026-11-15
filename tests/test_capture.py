"""Tests proving a capture records what Facebook sends for a reel, and keeps it to its owner."""

import json
import os
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import elder_sign.platforms.facebook.capture as facebook_capture
from elder_sign.browser import BrowserSettings
from elder_sign.cli import main
from elder_sign.platforms.facebook.capture import Recorder, capture, default_capture_path

VARIABLES = json.dumps({"feedbackID": "ZmVlZGJhY2s6MTIz", "first": 10})

FORM = f"fb_api_req_friendly_name=CommentsRootQuery&doc_id=24680&variables={VARIABLES}"


def observe(
    recorder: Recorder,
    *,
    url: str = "https://www.facebook.com/api/graphql/",
    method: str = "POST",
    resource_type: str = "fetch",
    post_data: str | None = FORM,
    body: str = '{"data": {}}',
) -> tuple[bool, list[bool]]:
    """Show the recorder one response; return whether it kept it, and whether it read the body."""

    read: list[bool] = []

    def text() -> str:
        read.append(True)
        return body

    kept = recorder.observe(
        url=url,
        method=method,
        resource_type=resource_type,
        status=200,
        post_data=post_data,
        body=text,
    )

    return kept, read


# What a capture keeps


def test_a_graphql_response_is_kept_with_the_query_it_answered() -> None:
    recorder = Recorder()

    kept, _ = observe(recorder)
    (record,) = recorder.records

    assert kept
    assert record == {
        "kind": "graphql",
        "url": "https://www.facebook.com/api/graphql/",
        "status": 200,
        "friendly_name": "CommentsRootQuery",
        "doc_id": "24680",
        "variables": {"feedbackID": "ZmVlZGJhY2s6MTIz", "first": 10},
        "body": '{"data": {}}',
    }


def test_a_page_is_kept_because_the_first_comments_arrive_inside_it() -> None:
    recorder = Recorder()

    kept, _ = observe(
        recorder,
        url="https://www.facebook.com/reel/123",
        method="GET",
        resource_type="document",
        post_data=None,
        body="<html></html>",
    )

    assert kept
    assert recorder.records == [
        {
            "kind": "document",
            "url": "https://www.facebook.com/reel/123",
            "status": 200,
            "body": "<html></html>",
        }
    ]


@pytest.mark.parametrize(
    ("url", "method", "resource_type"),
    [
        ("https://scontent.xx.fbcdn.net/v/t39/1_n.jpg", "GET", "image"),
        ("https://www.facebook.com/api/graphql/", "GET", "fetch"),
        ("https://www.facebook.com/ajax/bz", "POST", "fetch"),
    ],
    ids=["image", "graphql by get", "other post"],
)
def test_anything_else_is_passed_over_without_reading_its_body(
    url: str,
    method: str,
    resource_type: str,
) -> None:
    recorder = Recorder()

    kept, read = observe(recorder, url=url, method=method, resource_type=resource_type)

    assert not kept
    assert read == []
    assert recorder.records == []


def test_variables_that_are_not_json_are_kept_as_sent() -> None:
    recorder = Recorder()

    observe(recorder, post_data="doc_id=1&variables=not-json")

    assert recorder.records[0]["variables"] == "not-json"
    assert recorder.records[0]["friendly_name"] is None


# Where it is kept


def test_a_capture_is_readable_by_its_owner_alone(
    tmp_path: Path,
) -> None:
    recorder = Recorder()
    observe(recorder)

    path = recorder.write(tmp_path / "deep" / "capture.jsonl")

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [json.loads(line)["kind"] for line in path.read_text().splitlines()] == ["graphql"]


def test_captures_live_in_elder_signs_own_folder_named_by_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    path = default_capture_path(datetime(2026, 11, 15, 4, 30, 5, tzinfo=UTC))

    assert path == tmp_path / ".elder-sign" / "captures" / "facebook-20261115T043005Z.jsonl"


# The command


def test_the_capture_command_says_to_scrub_before_sharing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Any] = []

    def fake_capture(
        url: str, output: Path, settings: BrowserSettings, *, wait: Any
    ) -> tuple[Path, int]:
        calls.append((url, output))
        return output, 7

    monkeypatch.setattr(facebook_capture, "capture", fake_capture)
    out = tmp_path / "c.jsonl"

    status = main(
        ["capture", "facebook", "https://www.facebook.com/reel/1", "--out", str(out)],
        wait=lambda: None,
    )
    printed = capsys.readouterr().out

    assert status == 0
    assert calls == [("https://www.facebook.com/reel/1", out)]
    assert "Expand every comment and every reply" in printed
    assert "Captured 7 responses" in printed
    assert "scrub it" in printed


# A real browser


class Facebookish(BaseHTTPRequestHandler):
    """Serve a page that asks for its comments by GraphQL, as Facebook's does."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        page = (
            b"<html><body><script>"
            b"fetch('/api/graphql/', {method: 'POST', headers: "
            b"{'Content-Type': 'application/x-www-form-urlencoded'}, "
            b"body: 'fb_api_req_friendly_name=CommentsRootQuery&doc_id=24680&variables=%7B%7D'})"
            b".then(r => r.text()).then(t => document.title = 'done')"
            b"</script></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'for (;;);{"data": {"comments": []}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def server() -> Iterator[str]:
    """Serve the Facebook-ish page on a local port for one test."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Facebookish)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/reel/1"
    finally:
        httpd.shutdown()


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ELDER_SIGN_BROWSER_TESTS") != "1",
    reason="opens a real browser; set ELDER_SIGN_BROWSER_TESTS=1",
)
def test_a_real_browser_capture_records_the_page_and_its_graphql(
    tmp_path: Path,
) -> None:
    settings = BrowserSettings(profile=tmp_path / "profile", channel=None)

    with server() as url:
        written, count = capture(
            url, tmp_path / "capture.jsonl", settings, wait=lambda: time.sleep(2), show=False
        )

    kinds = [json.loads(line)["kind"] for line in written.read_text().splitlines()]
    graphql = [json.loads(line) for line in written.read_text().splitlines() if '"graphql"' in line]

    assert count == len(kinds)
    assert set(kinds) == {"document", "graphql"}
    assert graphql[0]["friendly_name"] == "CommentsRootQuery"
    assert graphql[0]["body"].startswith("for (;;);")
