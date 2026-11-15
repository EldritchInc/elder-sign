"""Tests proving Elder Sign's browser: its own profile, its human pace, and signing in."""

import os
import random
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import elder_sign.platforms.facebook.session as facebook_session
from elder_sign.browser import (
    MISSING_PLAYWRIGHT,
    PROFILE_ENVIRONMENT_VARIABLE,
    BrowserError,
    BrowserSettings,
    Pacer,
    open_browser,
    profile_path,
)
from elder_sign.cli import main
from elder_sign.platforms.facebook.session import FACEBOOK_URL, account_of, login

# The profile


def test_the_profile_is_named_by_its_variable_or_lives_in_the_home_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert profile_path({}) == tmp_path / ".elder-sign" / "browser"
    assert profile_path({PROFILE_ENVIRONMENT_VARIABLE: " "}) == tmp_path / ".elder-sign" / "browser"
    assert profile_path({PROFILE_ENVIRONMENT_VARIABLE: "/x/profile"}) == Path("/x/profile")


def test_the_browser_is_the_persons_own_chrome_shown_by_default() -> None:
    settings = BrowserSettings(profile=Path("/x"))

    assert (settings.channel, settings.headless) == ("chrome", False)


def test_without_playwright_the_browser_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(BrowserError) as raised, open_browser(BrowserSettings(profile=tmp_path)):
        pass

    assert str(raised.value) == MISSING_PLAYWRIGHT


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ELDER_SIGN_BROWSER_TESTS") != "1",
    reason="opens a real browser; set ELDER_SIGN_BROWSER_TESTS=1",
)
def test_a_real_browser_opens_with_the_profile_and_keeps_it(
    tmp_path: Path,
) -> None:
    settings = BrowserSettings(profile=tmp_path / "profile", channel=None, headless=True)

    with open_browser(settings) as context:
        page = context.new_page()
        page.goto("data:text/html,<title>Elder Sign</title>")

        assert page.title() == "Elder Sign"

    assert any((tmp_path / "profile").iterdir())


# The pace


class Sleeps:
    """Record every sleep instead of sleeping."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def test_every_pause_waits_between_its_bounds_unless_it_rests() -> None:
    sleeps = Sleeps()
    pacer = Pacer(2.0, 5.0, rest_every=1_000_000, sleep=sleeps, chance=random.Random(1))

    waited = [pacer.pause() for _ in range(200)]

    assert waited == sleeps.seconds
    assert all(2.0 <= seconds <= 5.0 for seconds in waited)
    assert len(set(waited)) == 200


def test_now_and_then_a_pause_is_a_longer_rest() -> None:
    sleeps = Sleeps()
    pacer = Pacer(1.0, 1.0, rest_every=4, rest_factor=3.0, sleep=sleeps, chance=random.Random(7))

    for _ in range(2000):
        pacer.pause()

    rests = sleeps.seconds.count(3.0)

    assert set(sleeps.seconds) == {1.0, 3.0}
    assert 400 < rests < 600


def test_a_pause_can_be_scaled() -> None:
    sleeps = Sleeps()
    pacer = Pacer(1.0, 1.0, rest_every=1_000_000, sleep=sleeps, chance=random.Random(3))

    assert pacer.pause(2.5) == 2.5


@pytest.mark.parametrize(
    "arguments",
    [
        {"minimum": -1.0, "maximum": 1.0},
        {"minimum": 3.0, "maximum": 2.0},
        {"minimum": 1.0, "maximum": 2.0, "rest_every": 0},
        {"minimum": 1.0, "maximum": 2.0, "rest_factor": 0.5},
    ],
    ids=["negative", "inverted", "no rests", "short rests"],
)
def test_a_pace_that_makes_no_sense_is_refused(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        Pacer(**arguments)


# Signing in to Facebook


@pytest.mark.parametrize(
    ("cookies", "account"),
    [
        ([{"name": "c_user", "domain": ".facebook.com", "value": "1000123"}], "1000123"),
        ([{"name": "xs", "domain": ".facebook.com", "value": "x"}], None),
        ([{"name": "c_user", "domain": ".evil.com", "value": "1000123"}], None),
        ([{"name": "c_user", "domain": ".facebook.com", "value": ""}], None),
        ([], None),
    ],
    ids=["signed in", "other cookie", "other site", "empty", "no cookies"],
)
def test_the_account_is_read_from_facebooks_own_cookie(
    cookies: list[dict[str, str]],
    account: str | None,
) -> None:
    assert account_of(cookies) == account


class FakePage:
    """A page that records where it went."""

    def __init__(self) -> None:
        self.visited: list[str] = []

    def goto(self, url: str) -> None:
        self.visited.append(url)


class FakeContext:
    """A browser context whose cookies a test sets."""

    def __init__(self, cookies: list[dict[str, str]]) -> None:
        self._cookies = cookies
        self.pages: list[FakePage] = [FakePage()]

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def cookies(self, url: str) -> list[dict[str, str]]:
        assert url == FACEBOOK_URL
        return self._cookies


@pytest.fixture
def opened(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Stand in for the browser, recording how it was opened and what it opened."""

    record: list[Any] = []
    cookies = [{"name": "c_user", "domain": ".facebook.com", "value": "1000123"}]

    @contextmanager
    def fake_open(settings: BrowserSettings) -> Iterator[FakeContext]:
        context = FakeContext(cookies)
        record.extend([settings, context])
        yield context

    monkeypatch.setattr(facebook_session, "open_browser", fake_open)

    return record


def test_login_opens_facebook_shown_waits_for_the_person_and_says_as_whom(
    opened: list[Any],
    tmp_path: Path,
) -> None:
    waited: list[bool] = []
    hidden = BrowserSettings(profile=tmp_path, headless=True)

    account = login(hidden, wait=lambda: waited.append(True))
    settings, context = opened

    assert account == "1000123"
    assert waited == [True]
    assert settings.headless is False
    assert settings.profile == tmp_path
    assert context.pages[0].visited == [FACEBOOK_URL]


def test_the_login_command_says_who_signed_in(
    opened: list[Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(PROFILE_ENVIRONMENT_VARIABLE, str(tmp_path))

    status = main(["login", "facebook"], wait=lambda: None)
    output = capsys.readouterr().out

    assert status == 0
    assert "never sees your password" in output
    assert "Signed in to Facebook as account 1000123." in output


def test_the_login_command_fails_when_nobody_signed_in(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(facebook_session, "login", lambda settings, wait: None)

    assert main(["login", "facebook"], wait=lambda: None) == 1
    assert "not signed in" in capsys.readouterr().err


def test_the_login_command_fails_when_the_browser_cannot_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(settings: BrowserSettings, wait: Any) -> str:
        raise BrowserError(MISSING_PLAYWRIGHT)

    monkeypatch.setattr(facebook_session, "login", broken)

    assert main(["login", "facebook"], wait=lambda: None) == 1
    assert MISSING_PLAYWRIGHT in capsys.readouterr().err
