"""The `elder-sign` command."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from elder_sign import scrub
from elder_sign.browser import BrowserError, BrowserSettings, profile_path
from elder_sign.platforms.facebook import capture as facebook_capture
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


def _capture(
    options: argparse.Namespace,
    *,
    wait: Callable[[], object],
) -> int:
    """Record what Facebook sends for one reel while the person expands its comments."""

    output = Path(options.out) if options.out else facebook_capture.default_capture_path()

    print(
        "Opening the reel in Elder Sign's browser.\n"
        "Expand every comment and every reply there, by hand, until nothing is folded away.\n"
        "Then come back here and press Enter."
    )

    try:
        written, count = facebook_capture.capture(
            options.url, output, BrowserSettings(profile=profile_path()), wait=wait
        )
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Captured {count} responses in {written}.")
    print("It holds real names and comments: scrub it before it goes anywhere.")

    return 0


def _scrub(
    options: argparse.Namespace,
) -> int:
    """Write a capture scrubbed of every name, word and ID, beside it or where asked."""

    source = Path(options.capture)
    output = Path(options.out) if options.out else source.with_suffix(".scrubbed.jsonl")
    records = [
        json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    try:
        lines = list(scrub.scrub(records))
    except scrub.LeakError as exc:
        print(exc, file=sys.stderr)
        return 1

    output.write_text("".join(lines), encoding="utf-8")
    print(f"Scrubbed {len(lines)} responses into {output}.")

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

    capture = commands.add_parser(
        "capture",
        help="Record what a platform sends for one post, to learn its shapes.",
    )
    capture.add_argument("platform", choices=PLATFORMS)
    capture.add_argument("url", help="The post to open, such as a reel's link.")
    capture.add_argument("--out", help="Where to write the capture.")

    scrubbing = commands.add_parser(
        "scrub",
        help="Make a capture safe to share: every name, word and ID replaced.",
    )
    scrubbing.add_argument("capture", help="The capture to scrub.")
    scrubbing.add_argument("--out", help="Where to write the scrubbed capture.")

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

    if options.command == "capture":
        return _capture(options, wait=wait)

    if options.command == "scrub":
        return _scrub(options)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
