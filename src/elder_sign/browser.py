"""The browser Elder Sign reads platforms through: the person's own Chrome, with its own profile.

Elder Sign never asks for a password. It drives the person's installed Chrome
with a profile of its own, at `~/.elder-sign/browser` (or `ELDER_SIGN_PROFILE`),
where they sign in once themselves with `elder-sign login`. That profile is
kept apart from their everyday Chrome, so Elder Sign never fights it for a
locked profile, and never sees anything they did not sign in to for it.

Everything Elder Sign does in the browser waits a human while first: the
`Pacer` sleeps a random time between actions, and now and then a longer one,
so it reads a page at the pace a person moderating their own comments would.

Playwright is an optional dependency, installed with `elder-sign[facebook]`,
and is imported only when a browser is opened.
"""

import os
import random
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

PROFILE_ENVIRONMENT_VARIABLE: Final = "ELDER_SIGN_PROFILE"
"""The variable naming the browser profile Elder Sign signs in with."""

CHROME: Final = "chrome"
"""The Playwright channel of the person's installed Google Chrome."""

MISSING_PLAYWRIGHT: Final = (
    "Elder Sign reads platforms through a browser, which needs Playwright: "
    "python -m pip install 'elder-sign[facebook]'"
)


class BrowserError(RuntimeError):
    """Raised when the browser cannot be opened."""


def profile_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the browser profile: `ELDER_SIGN_PROFILE`, or `~/.elder-sign/browser`."""

    environment = os.environ if environment is None else environment
    configured = environment.get(PROFILE_ENVIRONMENT_VARIABLE, "").strip()

    if configured:
        return Path(configured).expanduser()

    return Path.home() / ".elder-sign" / "browser"


@dataclass(frozen=True)
class BrowserSettings:
    """How Elder Sign opens its browser."""

    profile: Path = field(default_factory=profile_path)
    channel: str | None = CHROME
    """The installed browser to drive; none drives Playwright's own Chromium."""

    headless: bool = False
    """Whether the window stays hidden: never while the person signs in."""


@contextmanager
def open_browser(
    settings: BrowserSettings | None = None,
) -> Iterator["BrowserContext"]:
    """Open the browser with Elder Sign's profile, and close it after.

    Raises:
        BrowserError: Playwright is not installed, or the browser cannot be opened.
    """

    settings = settings or BrowserSettings()

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise BrowserError(MISSING_PLAYWRIGHT) from exc

    settings.profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(settings.profile),
                channel=settings.channel,
                headless=settings.headless,
                no_viewport=True,
            )
        except Error as exc:
            raise BrowserError(f"The browser could not be opened: {exc.message}") from exc

        try:
            yield context
        finally:
            context.close()


class Pacer:
    """Wait a human while between browser actions.

    Each pause lasts a random time between `minimum` and `maximum` seconds,
    and one in `rest_every` pauses on average is a rest `rest_factor` times as
    long, as a person stops to read.
    """

    def __init__(
        self,
        minimum: float = 1.5,
        maximum: float = 4.5,
        *,
        rest_every: int = 8,
        rest_factor: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        chance: random.Random | None = None,
    ) -> None:
        if not 0 <= minimum <= maximum:
            raise ValueError("A pause's minimum must be at least 0 and at most its maximum.")

        if rest_every < 1 or rest_factor < 1:
            raise ValueError("Rests must come at least every pause, and last at least as long.")

        self._minimum = minimum
        self._maximum = maximum
        self._rest_every = rest_every
        self._rest_factor = rest_factor
        self._sleep = sleep
        self._chance = chance or random.SystemRandom()

    def pause(
        self,
        scale: float = 1.0,
    ) -> float:
        """Wait a while, `scale` times the usual, and return how long."""

        seconds = self._chance.uniform(self._minimum, self._maximum) * scale

        if self._chance.random() < 1 / self._rest_every:
            seconds *= self._rest_factor

        self._sleep(seconds)

        return seconds
