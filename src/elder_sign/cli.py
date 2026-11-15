"""The `elder-sign` command."""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from elder_sign import canary as canaries
from elder_sign import judging, scrub
from elder_sign.browser import BrowserError, BrowserSettings, profile_path
from elder_sign.platforms import Post
from elder_sign.platforms.facebook import capture as facebook_capture
from elder_sign.platforms.facebook import reader as facebook_reader
from elder_sign.platforms.facebook import session as facebook_session
from elder_sign.platforms.facebook.parse import REEL_PATH
from elder_sign.shared import store

PLATFORMS = ("facebook",)

FROZEN_EXIT: Final = 3
"""The exit status of a canary check that froze Elder Sign on a platform."""


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

    output = Path(options.out) if options.out else scans_path() / f"facebook-{reel_id}.json"
    items = []
    stills = []

    try:
        with facebook_reader.facebook(_settings(options), keep_capture=kept) as platform:
            scan = platform.scan(post)
            notices = platform.notices

            if options.media:
                items = [item for comment in scan["comments"] for item in comment.get("media", [])]
                stills = [platform.fetch_media(item) for item in items]
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    write_private(output, json.dumps(scan, ensure_ascii=False, indent=2) + "\n")
    comments = scan["comments"]
    replies = sum(1 for comment in comments if comment["parent_id"] is not None)
    media = sum(1 for comment in comments if comment.get("media"))
    hidden = sum(1 for comment in comments if comment.get("hidden"))
    counted = scan.get("comment_count")

    print(
        f"Read {len(comments)} comments on reel {reel_id}: {len(comments) - replies} top-level, "
        f"{replies} replies, {media} with a GIF, sticker or image."
    )
    print(
        f"{len(comments) - hidden} visible + {hidden} hidden = {len(comments)}; "
        f"{counted_line(counted, len(comments))}"
    )

    for notice in notices:
        print(f"Facebook says: {notice}")

    print(f"Kept in {output}, readable by you alone: it holds real names and comments.")

    if options.media:
        folder = output.with_name(f"{output.stem}-media")
        fetched = 0

        for index, (item, still) in enumerate(zip(items, stills, strict=True), start=1):
            if still is None:
                continue

            extension = still.media_type.removeprefix("image/").replace("jpeg", "jpg")
            write_private_bytes(folder / f"{index:03}-{item['kind']}.{extension}", still.content)
            fetched += 1

        print(
            f"Fetched {fetched} of {len(items)} GIFs, stickers and images as stills"
            + (f", kept in {folder}." if fetched else ".")
        )

    if kept is not None:
        print(f"Everything Facebook sent is in {kept}; scrub it before it goes anywhere.")

    return 0


def _buttons(
    options: argparse.Namespace,
) -> int:
    """List every button a reel shows, before and after its comments are opened, by name."""

    found = REEL_PATH.search(options.url)

    if found is None:
        print(f"{options.url} is not a link to a reel.", file=sys.stderr)
        return 1

    url = facebook_reader.REEL_URL.format(reel_id=found.group(1))

    try:
        with facebook_reader.facebook_browser(_settings(options)) as browser:
            visited = browser.visit(url)
            details = facebook_reader.FacebookPlatform.details_of(found.group(1), visited)
            closed = browser.button_names()
            browser.open_comments(details.comment_count if details else None)
            opened = browser.button_names()
    except BrowserError as exc:
        print(exc, file=sys.stderr)
        return 1

    for title, names in (("Before the comments are opened:", closed), ("After:", opened)):
        print(title)

        for name, count in Counter(names).items():
            print(f"{count:>3}  {name}")

    return 0


def counted_line(
    counted: int | None,
    read: int,
) -> str:
    """Return what Facebook counts on a reel, and how many of those it never served."""

    if counted is None:
        return "Facebook counts none."

    if counted <= read:
        return f"Facebook counts {counted}."

    return (
        f"Facebook counts {counted}: {counted - read} counted but not served "
        "(deleted, orphaned or from blocked accounts)."
    )


def _judge(
    options: argparse.Namespace,
    *,
    runner: judging.Runner | None = None,
) -> int:
    """Judge every comment on one reel, describing its new media first, changing nothing there."""

    found = REEL_PATH.search(options.url)

    if found is None:
        print(f"{options.url} is not a link to a reel.", file=sys.stderr)
        return 1

    reel_id = found.group(1)
    post = Post(
        platform="facebook", post_id=reel_id, url=facebook_reader.REEL_URL.format(reel_id=reel_id)
    )
    os.environ.setdefault(store.DATABASE_ENVIRONMENT_VARIABLE, str(store.database_path()))
    connection = store.connect()

    try:
        canaries.ensure_unfrozen(connection, "facebook")
    except canaries.FrozenError as exc:
        print(exc, file=sys.stderr)
        return FROZEN_EXIT
    finally:
        connection.close()

    try:
        with facebook_reader.facebook(_settings(options)) as platform:
            scan = platform.scan(post)
            judgment = judging.judge(scan, platform, runner or judging.AzathothRunner())
    except (BrowserError, judging.WorkflowError) as exc:
        print(exc, file=sys.stderr)
        return 1

    output = (
        Path(options.out) if options.out else scans_path() / f"facebook-{reel_id}-verdicts.json"
    )
    report = {
        "outcome": judgment.outcome,
        "described": judgment.described,
        "undescribed_media": list(judgment.undescribed),
        "held": list(judgment.held),
    }
    write_private(output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    counts = judgment.counts()
    model = judgment.outcome.get("model", {})
    layers = judgment.outcome.get("layers", {})
    by_layer = ", ".join(f"{count} by {layer}" for layer, count in sorted(layers.items()))
    print(
        f"Judged {sum(counts.values())} comments on reel {reel_id}: {counts['hostile']} hostile, "
        f"{counts['friendly']} friendly, {counts['uncertain']} uncertain."
    )
    print(f"Decided {by_layer or 'nothing'}.")
    groups = _plural(int(model.get("groups", 0)), "group", "groups")
    new_media = _plural(judgment.described, "new GIF, sticker or image", "new media")
    print(
        f"Asked a model about {groups} ({model.get('unreadable', 0)} unreadable); "
        f"described {new_media}."
    )

    if judgment.held:
        held = _plural(len(judgment.held), "comment", "comments")
        missing = _plural(len(judgment.undescribed), "GIF, sticker or image", "pieces of media")
        print(f"Held {held} for the next run: {missing} could not be fetched or described.")

    print(f"Kept in {output}, readable by you alone. Nothing on Facebook was changed.")

    return 0


def _forget_verdicts(
    options: argparse.Namespace,
) -> int:
    """Forget what a model decided about comments, so they are judged again; keep yours."""

    if not options.model:
        print(
            "Say which verdicts to forget: --model forgets a model's, never yours.",
            file=sys.stderr,
        )
        return 1

    connection = store.connect()

    try:
        forgotten = store.forget_model_verdicts(connection, options.policy)
    finally:
        connection.close()

    print(
        f"Forgot {_plural(forgotten, 'verdict', 'verdicts')} a model gave on comments. "
        "Yours, and what a model saw in GIFs and images, are kept; "
        "the next judge asks a model again."
    )

    return 0


def _plural(
    count: int,
    one: str,
    many: str,
) -> str:
    """Return a count and the word for that many."""

    return f"{count} {one if count == 1 else many}"


def _set_canary(
    options: argparse.Namespace,
) -> int:
    """Keep one of the person's reels, as it reads now, as the canary Facebook is checked by."""

    found = REEL_PATH.search(options.url)

    if found is None:
        print(f"{options.url} is not a link to a reel.", file=sys.stderr)
        return 1

    reel_id = found.group(1)
    post = Post(
        platform="facebook", post_id=reel_id, url=facebook_reader.REEL_URL.format(reel_id=reel_id)
    )
    connection = store.connect()

    try:
        with facebook_reader.facebook(_settings(options)) as platform:
            canary = canaries.set_canary(connection, platform, post)
    except (BrowserError, canaries.CanaryError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        connection.close()

    totals = canary.totals()
    print(
        f"The canary is reel {reel_id}: {_plural(len(canary.comments), 'comment', 'comments')}, "
        f"{_plural(totals.get('reply', 0), 'reply', 'replies')}, "
        f"{_plural(totals.get('mention', 0), 'mention', 'mentions')}, "
        f"{_plural(totals.get('media', 0), 'GIF, sticker or image', 'GIFs, stickers or images')} "
        f"({totals.get('still', 0)} fetched), {totals.get('hidden', 0)} hidden."
    )

    blind = [
        canaries.BLIND_SPOTS[spot]
        for spot in canary.blind_spots()
        if not (spot == "still" and "media" in canary.blind_spots())
    ]

    if blind:
        named = blind[0] if len(blind) == 1 else ", ".join(blind[:-1]) + ", or " + blind[-1]
        print(
            f"It has no {named}, so a change to how Facebook shows those would go unnoticed. "
            "A canary with a reply, a mention, a GIF and a comment you hid catches the most."
        )

    return 0


def _check_canary(
    options: argparse.Namespace,
) -> int:
    """Read the canary again, and freeze Elder Sign on Facebook if it reads less than it did."""

    connection = store.connect()

    try:
        was_frozen = canaries.frozen_reason(connection, "facebook") is not None

        with facebook_reader.facebook(_settings(options)) as platform:
            problems = canaries.check(connection, platform)
    except (BrowserError, canaries.CanaryError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        connection.close()

    if not problems:
        print("The canary reads as it did: Facebook has not changed what Elder Sign reads.")

        if was_frozen:
            print("Elder Sign is no longer frozen on facebook.")

        return 0

    print("The canary no longer reads as it did:")

    for problem in problems:
        print(f"  {problem}")

    print(
        "Elder Sign is frozen on facebook: it hides, deletes and unhides nothing there "
        "until a check passes.\n"
        "If you changed the canary yourself, set it again: "
        "elder-sign canary set facebook <link to the reel>"
    )

    return FROZEN_EXIT


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


def write_private_bytes(
    path: Path,
    content: bytes,
) -> None:
    """Write a file readable and writable by its owner alone, in a folder only they can open."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)

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
    scanning.add_argument(
        "--media",
        action="store_true",
        help="Also fetch every GIF, sticker and image, as the stills a model would be shown.",
    )

    judging_command = commands.add_parser(
        "judge",
        help="Judge every comment on one reel, describing new media first, changing nothing.",
    )
    judging_command.add_argument("platform", choices=PLATFORMS)
    judging_command.add_argument("url", help="The reel's link.")
    judging_command.add_argument("--out", help="Where to keep the verdicts.")
    judging_command.add_argument(
        "--hidden", action="store_true", help="Keep the browser window hidden."
    )

    verdicts = commands.add_parser(
        "verdicts",
        help="Manage the verdicts Elder Sign remembers.",
    )
    verdict_commands = verdicts.add_subparsers(dest="verdicts_command", required=True)
    forgetting = verdict_commands.add_parser(
        "forget",
        help="Forget verdicts, so the comments are judged again: a model's, never yours.",
    )
    forgetting.add_argument(
        "--model", action="store_true", help="Forget every verdict a model gave on comments."
    )
    forgetting.add_argument("--policy", help="Forget only those under this policy.")

    canary = commands.add_parser(
        "canary",
        help="Check Facebook against a reel of yours, to know when it changed under Elder Sign.",
    )
    canary_commands = canary.add_subparsers(dest="canary_command", required=True)
    setting = canary_commands.add_parser(
        "set",
        help="Keep a reel of yours, as it reads now, as the canary.",
    )
    setting.add_argument("platform", choices=PLATFORMS)
    setting.add_argument("url", help="The reel's link: one with a reply, a mention and a GIF.")
    setting.add_argument("--hidden", action="store_true", help="Keep the browser window hidden.")
    checking = canary_commands.add_parser(
        "check",
        help="Read the canary again; freeze Elder Sign if it reads less (exit status 3).",
    )
    checking.add_argument("platform", choices=PLATFORMS)
    checking.add_argument("--hidden", action="store_true", help="Keep the browser window hidden.")

    buttons = commands.add_parser(
        "buttons",
        help="List every button a reel shows, by name, to see how Facebook names them today.",
    )
    buttons.add_argument("platform", choices=PLATFORMS)
    buttons.add_argument("url", help="The reel's link.")
    buttons.add_argument("--hidden", action="store_true", help="Keep the browser window hidden.")

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

    if options.command == "buttons":
        return _buttons(options)

    if options.command == "judge":
        return _judge(options)

    if options.command == "verdicts":
        return _forget_verdicts(options)

    if options.command == "canary" and options.canary_command == "set":
        return _set_canary(options)

    if options.command == "canary":
        return _check_canary(options)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
