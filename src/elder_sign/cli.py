"""The `elder-sign` command."""

import argparse
import sys
from collections.abc import Callable, Sequence

from elder_sign.browser import BrowserError, BrowserSettings, profile_path
from elder_sign.platforms.facebook import session as facebook_session

PLATFORMS = ("facebook",)


def _login(
    options: argparse.Namespace,
    *,
    wait: Callable[[], object],
) -> int:
    """Open a platform for the person to sign in to, and say whether they did."""

    settings = BrowserSettings(profile=profile_path())

    print(
        f"Opening Facebook in Elder Sign's browser ({settings.profile}).\n"
        "Sign in there yourself; Elder Sign never sees your password.\n"
        "When you see your feed, come back here and press Enter."
    )

    try:
        account = facebook_session.login(settings, wait=wait)
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    if account is None:
        print("Elder Sign's browser is not signed in to Facebook.", file=sys.stderr)
        return 1

    print(f"Signed in to Facebook as account {account}.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the parser of every `elder-sign` command."""

    parser = argparse.ArgumentParser(
        prog="elder-sign",
        description="Ward your posts against troll swarms.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser(
        "login",
        help="Sign in to a platform, once, in Elder Sign's own browser profile.",
    )
    login.add_argument("platform", choices=PLATFORMS)

    return parser


def main(
    arguments: Sequence[str] | None = None,
    *,
    wait: Callable[[], object] = input,
) -> int:
    """Run one `elder-sign` command, returning its exit status."""

    options = build_parser().parse_args(arguments)

    if options.command == "login":
        return _login(options, wait=wait)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
