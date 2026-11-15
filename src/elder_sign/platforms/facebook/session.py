"""Signing in to Facebook, once, in Elder Sign's own browser profile.

`elder-sign login facebook` opens Facebook in Elder Sign's browser, and the
person signs in there themselves, with whatever two-factor step their account
asks for. Elder Sign never sees their password. The browser keeps the
session, so every later run is already signed in.

Facebook marks a signed-in session with its `c_user` cookie, whose value is
the account's ID; that is how Elder Sign tells whether the profile is signed
in, and as whom.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Final

from elder_sign.browser import BrowserSettings, open_browser

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

FACEBOOK_URL: Final = "https://www.facebook.com/"

ACCOUNT_COOKIE: Final = "c_user"
"""The cookie whose value is the signed-in account's ID."""

FACEBOOK_DOMAINS: Final = (".facebook.com", "facebook.com", "www.facebook.com")


def account_of(
    cookies: Iterable[Any],
) -> str | None:
    """Return the ID of the account a browser's cookies are signed in to Facebook as, if any."""

    for cookie in cookies:
        if (
            cookie.get("name") == ACCOUNT_COOKIE
            and cookie.get("domain") in FACEBOOK_DOMAINS
            and cookie.get("value")
        ):
            return str(cookie["value"])

    return None


def signed_in_account(
    context: "BrowserContext",
) -> str | None:
    """Return the ID of the account a browser is signed in to Facebook as, if any."""

    return account_of(context.cookies(FACEBOOK_URL))


def login(
    settings: BrowserSettings | None = None,
    *,
    wait: Callable[[], object] = input,
) -> str | None:
    """Open Facebook for the person to sign in, wait until they say they are done, and say as whom.

    The browser is always shown, so the person can sign in. `wait` returns
    once they are done; by default, when they press Enter.
    """

    settings = settings or BrowserSettings()
    shown = BrowserSettings(profile=settings.profile, channel=settings.channel, headless=False)

    with open_browser(shown) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(FACEBOOK_URL)
        wait()

        return signed_in_account(context)
