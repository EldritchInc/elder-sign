"""The `elder-sign` command."""

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from elder_sign import scrub
from elder_sign.browser import BrowserError, BrowserSettings, profile_path
from elder_sign.platforms import Post
from elder_sign.platforms.facebook import capture as facebook_capture
from elder_sign.platforms.facebook import reader as facebook_reader
from elder_sign.platforms.facebook import session as facebook_session
from elder_sign.platforms.facebook.parse import REEL_PATH

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
        lines = list(scrub.scrub(records, everything=options.everything))
    except scrub.LeakError as exc:
        print(exc, file=sys.stderr)
        return 1

    output.write_text("".join(lines), encoding="utf-8")
    print(f"Scrubbed {len(lines)} responses into {output}.")

    return 0


def _settings(
    options: argparse.Namespace,
) -> BrowserSettings:
    """Return the browser a command reads through: shown, unless it was asked to hide."""

    return BrowserSettings(profile=profile_path(), headless=bool(getattr(options, "hidden", False)))


def _read(
    options: argparse.Namespace,
) -> int:
    """List the person's recent reels, with each one's comment count, changing nothing."""

    try:
        with facebook_reader.facebook(_settings(options)) as platform:
            posts = platform.recent_posts(options.days)

            for post in posts:
                fingerprint = platform.fingerprint(post)
                published = post.published_at.date().isoformat() if post.published_at else "?"
                print(
                    f"{post.post_id}  published {published}  "
                    f"{fingerprint.comment_count} comments  newest {fingerprint.newest_comment_id}"
                )
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"{len(posts)} reels from the last {options.days} days.")

    return 0


def _scan(
    options: argparse.Namespace,
) -> int:
    """Read every comment and reply on one reel, and keep them where only their owner can read."""

    found = REEL_PATH.search(options.url)

    if found is None:
        print(f"{options.url} is not a link to a reel.", file=sys.stderr)
        return 1

    reel_id = found.group(1)
    post = Post(
        platform="facebook", post_id=reel_id, url=facebook_reader.REEL_URL.format(reel_id=reel_id)
    )

    kept = (
        facebook_capture.captures_path() / f"facebook-scan-{reel_id}.jsonl"
        if options.keep_capture
        else None
    )

    try:
        with facebook_reader.facebook(_settings(options), keep_capture=kept) as platform:
            scan = platform.scan(post)
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    output = Path(options.out) if options.out else scans_path() / f"facebook-{reel_id}.json"
    write_private(output, json.dumps(scan, ensure_ascii=False, indent=2) + "\n")
    comments = scan["comments"]
    replies = sum(1 for comment in comments if comment["parent_id"] is not None)
    media = sum(1 for comment in comments if comment.get("media"))

    print(
        f"Read {len(comments)} comments on reel {reel_id}: {len(comments) - replies} top-level, "
        f"{replies} replies, {media} with a GIF, sticker or image."
    )
    print(f"Kept in {output}, readable by you alone: it holds real names and comments.")

    if kept is not None:
        print(f"Everything Facebook sent is in {kept}; scrub it before it goes anywhere.")

    return 0


def scans_path() -> Path:
    """Return where scans are kept: `~/.elder-sign/scans`."""

    return Path.home() / ".elder-sign" / "scans"


def write_private(
    path: Path,
    text: str,
) -> None:
    """Write a file readable and writable by its owner alone."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)

    os.chmod(path, 0o600)


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
    scrubbing.add_argument(
        "--everything",
        action="store_true",
        help="Keep every response, not only those about comments.",
    )

    reading = commands.add_parser(
        "read",
        help="List your recent reels and their comment counts, changing nothing.",
    )
    reading.add_argument("platform", choices=PLATFORMS)
    reading.add_argument("--days", type=int, default=30, help="How far back to look.")
    reading.add_argument("--hidden", action="store_true", help="Keep the browser window hidden.")

    scanning = commands.add_parser(
        "scan",
        help="Read every comment and reply on one reel, changing nothing.",
    )
    scanning.add_argument("platform", choices=PLATFORMS)
    scanning.add_argument("url", help="The reel's link.")
    scanning.add_argument("--out", help="Where to keep the comments read.")
    scanning.add_argument("--hidden", action="store_true", help="Keep the browser window hidden.")
    scanning.add_argument(
        "--keep-capture",
        action="store_true",
        help="Also keep everything Facebook sent, to look into a scan that read too little.",
    )

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

    if options.command == "read":
        return _read(options)

    if options.command == "scan":
        return _scan(options)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
