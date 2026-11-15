"""Judge one scan of a post: describe its new media, then give every comment its verdict.

A scan is judged in three steps, each an Azathoth workflow:

1. `resolve-comments` decides what it can without a model, and names the
   GIFs, stickers and images no model has described yet;
2. each of those is fetched as a still through the platform, shown to a
   model that can see by `judge-media`, and its description kept by
   `record-media`, so it is never looked at again;
3. `moderate-scan` gives every comment its verdict, words judged beside the
   descriptions of their media, and remembers what a model decided.

A comment whose media, or whose parent's media, could not be described is
**held** for the next run rather than judged: judged without what it posts,
a GIF reply would be remembered as harmless forever. Judging changes nothing
on the platform; it only decides, and remembers what it decided.
"""

import contextlib
import io
import json
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from elder_sign.platforms.base import Comment, MediaItem, Platform, Scan
from elder_sign.shared.keys import media_key

RESOLVE_COMMENTS_ID: Final = "e1de7516-0000-4000-8000-000000000100"

JUDGE_MEDIA_ID: Final = "e1de7516-0000-4000-8000-000000000200"

RECORD_MEDIA_ID: Final = "e1de7516-0000-4000-8000-000000000210"

MODERATE_SCAN_ID: Final = "e1de7516-0000-4000-8000-000000000400"

WORKFLOW_NAMES: Final = {
    RESOLVE_COMMENTS_ID: "resolve-comments",
    JUDGE_MEDIA_ID: "judge-media",
    RECORD_MEDIA_ID: "record-media",
    MODERATE_SCAN_ID: "moderate-scan",
}

EXTENSIONS: Final = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
"""The extension a still is written with, so Azathoth reads it as the image it is."""


class WorkflowError(RuntimeError):
    """Raised when an Azathoth workflow cannot be run, or fails."""


class Runner(Protocol):
    """Runs Elder Sign's Azathoth workflows."""

    def run(
        self,
        workflow_id: str,
        payload: Any = None,
        *,
        input_file: Path | None = None,
    ) -> Any:
        """Run one workflow on a JSON input or an input file, and return its result.

        Raises:
            WorkflowError: The workflow could not be run, or failed.
        """


class AzathothRunner:
    """Runs workflows through Azathoth's own command, in this process, as `azathoth` would.

    Each runs on a thread of its own: Azathoth starts its own event loop, and
    the browser keeps one running on the thread that reads the platform.
    """

    def run(
        self,
        workflow_id: str,
        payload: Any = None,
        *,
        input_file: Path | None = None,
    ) -> Any:
        """Run one workflow with `azathoth workflow run`, and return its result.

        Raises:
            WorkflowError: The workflow could not be run, or failed.
        """

        from azathoth.cli import main

        source = (
            ["--input-file", str(input_file)]
            if input_file is not None
            else ["--input", json.dumps(payload, ensure_ascii=False)]
        )
        arguments = ["workflow", "run", workflow_id, *source, "--output", "result"]
        printed, complained = io.StringIO(), io.StringIO()

        def run_azathoth() -> int:
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(complained):
                return main(arguments)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="azathoth") as pool:
            status = pool.submit(run_azathoth).result()

        name = WORKFLOW_NAMES.get(workflow_id, workflow_id)

        if status != 0:
            detail = (complained.getvalue() or printed.getvalue()).strip()
            raise WorkflowError(f"Azathoth could not run {name}: {detail or f'status {status}'}")

        try:
            return json.loads(printed.getvalue())
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Azathoth's result of {name} is not JSON: {exc}") from exc


@dataclass(frozen=True)
class Judgment:
    """What judging one scan decided, and what it left for the next run."""

    outcome: dict[str, Any]
    """`moderate-scan`'s outcome: every judged comment's verdict and the layer that decided it."""

    described: int = 0
    """How many GIFs, stickers and images a model described in this run."""

    undescribed: Sequence[str] = field(default_factory=tuple)
    """The keys of the media that could not be fetched or described."""

    held: Sequence[str] = field(default_factory=tuple)
    """The comments held for the next run, because their media or their parent's is undescribed."""

    def counts(self) -> dict[str, int]:
        """Return how many comments were found friendly, hostile and uncertain."""

        counted = {"hostile": 0, "friendly": 0, "uncertain": 0}

        for entry in self.outcome.get("verdicts", []):
            counted[entry["verdict"]] = counted.get(entry["verdict"], 0) + 1

        return counted


def _keys(
    comment: Comment,
) -> set[str]:
    """Return the keys of a comment's media."""

    return {key for item in comment.get("media", []) if (key := media_key(item)) is not None}


def held_back(
    scan: Scan,
    undescribed: set[str],
) -> list[str]:
    """Return the comments whose own media, or whose parent's, is undescribed."""

    by_id = {comment["id"]: comment for comment in scan["comments"]}
    held = []

    for comment in scan["comments"]:
        parent = by_id.get(comment["parent_id"] or "")

        if _keys(comment) & undescribed or (parent is not None and _keys(parent) & undescribed):
            held.append(comment["id"])

    return held


def _describe(
    item: dict[str, Any],
    platform: Platform,
    runner: Runner,
    folder: Path,
) -> bool:
    """Fetch one undescribed GIF, sticker or image, have a model describe it, and keep that."""

    media: MediaItem = {"kind": str(item.get("kind") or "image"), "url": item.get("url")}
    still = platform.fetch_media(media)

    if still is None:
        return False

    path = folder / f"still{EXTENSIONS.get(still.media_type, '.png')}"
    path.write_bytes(still.content)

    try:
        judgment = runner.run(JUDGE_MEDIA_ID, input_file=path)
        recorded = runner.run(
            RECORD_MEDIA_ID,
            {"media_key": str(item["media_key"]), "judgment": judgment, "model": None},
        )
    except WorkflowError:
        return False
    finally:
        path.unlink(missing_ok=True)

    return isinstance(recorded, dict) and recorded.get("described") is True


def judge(
    scan: Scan,
    platform: Platform,
    runner: Runner,
) -> Judgment:
    """Judge every comment of a scan, describing its new media first; change nothing on it.

    Raises:
        WorkflowError: `resolve-comments` or `moderate-scan` could not be run.
    """

    resolution = runner.run(RESOLVE_COMMENTS_ID, scan)
    pending: list[dict[str, Any]] = list(resolution.get("undescribed_media", []))
    undescribed: set[str] = set()
    described = 0

    with tempfile.TemporaryDirectory(prefix="elder-sign-") as folder:
        for item in pending:
            if _describe(item, platform, runner, Path(folder)):
                described += 1
            else:
                undescribed.add(str(item["media_key"]))

    held = held_back(scan, undescribed)
    judged: Scan = {
        **scan,
        "comments": [comment for comment in scan["comments"] if comment["id"] not in held],
    }
    outcome = runner.run(MODERATE_SCAN_ID, judged)

    return Judgment(
        outcome=outcome,
        described=described,
        undescribed=tuple(sorted(undescribed)),
        held=tuple(held),
    )
