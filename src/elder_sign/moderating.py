"""Moderate one post: judge it, plan, act on the plan, confirm, log, and remember it clean.

`moderate` is Elder Sign's whole loop for one post, in order:

1. it refuses while Elder Sign is frozen on the platform: a misread page is
   never acted on;
2. it skips a post whose fingerprint is the one kept when it was last clean,
   unless forced: an unchanged post costs one look and nothing more;
3. it reads every comment and `judge`s them, then `plan`s, and drops what
   the log says is already done;
4. unless told to **apply**, it only logs the plan, every action a dry run;
5. applied, it logs each action as planned, does them all, reads the post
   again, and settles each in the log: done when the fresh read shows it
   took, failed with why when it did not. A delete Facebook did not offer,
   done as a hide, is logged as one;
6. it keeps the post's fingerprint as clean only when it acted and every
   action took, and nothing is left too new to act on, deferred over the
   caps, or held for its media: so the next run looks at it again until it is.

Unhiding is a dry run unless `apply_unhide` says otherwise, as the planner
keeps it.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from sqlite3 import Connection

from elder_sign.audit import pending, planned, settled
from elder_sign.canary import ensure_unfrozen
from elder_sign.judging import Judgment, Runner, judge
from elder_sign.planning import Action, Plan, PlanSettings, plan
from elder_sign.platforms.base import ActionResult, Platform, Post, Scan, confirm
from elder_sign.shared.store import read_fingerprint, transaction, write_fingerprint

INSTEAD = {"hide": "hidden", "delete": "deleted", "unhide": "unhidden"}
"""How an action done in place of another is told in the log."""


@dataclass(frozen=True)
class ModerationSettings:
    """What moderating may do."""

    apply: bool = False
    """Whether to act, or only log what would be done."""

    apply_unhide: bool = False
    """Whether to unhide for real what a filter hid that is friendly."""

    delete: bool = True
    """Whether what is hostile anywhere and aimed at her is deleted, or only hidden."""


@dataclass(frozen=True)
class Moderation:
    """What moderating one post found, did and left."""

    post: Post
    skipped: bool = False
    """Whether the post was unchanged since it was last clean, and so not looked at."""

    scan: Scan | None = None
    judgment: Judgment | None = None
    plan: Plan | None = None
    acted: tuple[tuple[Action, str, str | None], ...] = ()
    """Each action taken, how it ended (`done` or `failed`), and why."""

    dry_run: tuple[Action, ...] = ()
    clean: bool = False
    """Whether the post was remembered clean."""

    results: tuple[ActionResult, ...] = field(default=())

    def counts(self) -> dict[str, int]:
        """Return how many comments were deleted, hidden, unhidden, failed, or only dry run."""

        done = [action.action for action, status, _ in self.acted if status == "done"]

        return {
            "deleted": done.count("delete"),
            "hidden": done.count("hide"),
            "unhidden": done.count("unhide"),
            "failed": sum(1 for _, status, _ in self.acted if status == "failed"),
            "dry_run": len(self.dry_run),
        }


def _verdicts(
    judgment: Judgment,
) -> dict[str, str]:
    """Return each judged comment's verdict."""

    return {
        str(entry["comment_id"]): str(entry["verdict"])
        for entry in judgment.outcome.get("verdicts", [])
    }


def _settle(
    connection: Connection,
    logged: dict[str, int],
    acting: list[Action],
    results: list[ActionResult],
    rescan: Scan,
) -> list[tuple[Action, str, str | None]]:
    """Settle each action taken in the log, by what the platform said and the fresh read shows."""

    by_comment = {result.comment_id: result for result in results}
    confirmed = confirm(
        [(result.comment_id, result.action) for result in results if result.ok], rescan
    )
    acted: list[tuple[Action, str, str | None]] = []

    for action in acting:
        result = by_comment.get(action.comment_id)

        detail: str | None

        if result is None:
            status, detail = "failed", "the platform did not say how it went"
        elif not result.ok:
            status, detail = "failed", result.detail
        else:
            took, why = confirmed[action.comment_id]
            status = "done" if took else "failed"
            detail = why

            if took and result.action != action.action:
                detail = f"{INSTEAD.get(result.action, result.action)} instead: {result.detail}"

        settled(connection, logged[action.comment_id], status, detail)
        acted.append((action, status, detail))

    return acted


def moderate(
    connection: Connection,
    platform: Platform,
    post: Post,
    runner: Runner,
    *,
    now: datetime,
    settings: ModerationSettings | None = None,
    force: bool = False,
) -> Moderation:
    """Moderate one post as the module says, and return what was found, done and left.

    Raises:
        FrozenError: Elder Sign is frozen on the platform.
        WorkflowError: Judging could not be run.
    """

    settings = settings or ModerationSettings()
    ensure_unfrozen(connection, platform.name)
    kept = read_fingerprint(connection, platform.name, post.post_id)

    if not force and kept is not None and kept == platform.fingerprint(post):
        return Moderation(post=post, skipped=True)

    scan: Scan = platform.scan(post)
    scan["owner_id"] = scan.get("owner_id") or platform.account_id
    judgment = judge(scan, platform, runner)
    planned_now = pending(
        connection,
        platform.name,
        plan(
            scan,
            judgment.outcome,
            now=now,
            settings=PlanSettings(delete=settings.delete, unhide=settings.apply_unhide),
        ),
    )
    verdicts = _verdicts(judgment)
    acting = [action for action in planned_now.actions if settings.apply and not action.dry_run]
    dry_run = [
        replace(action, dry_run=True) for action in planned_now.actions if action not in acting
    ]
    logged: dict[str, int] = {}

    with transaction(connection):
        for action in [*dry_run, *acting]:
            entry = planned(
                connection, platform.name, post.post_id, action, verdicts.get(action.comment_id)
            )

            if not action.dry_run:
                logged[action.comment_id] = entry

    acted: list[tuple[Action, str, str | None]] = []
    results: list[ActionResult] = []

    if acting:
        hidden = {comment["id"] for comment in scan["comments"] if comment.get("hidden")}
        results = platform.act(post, [(a.comment_id, a.action) for a in acting], hidden)
        rescan = platform.scan(post)

        with transaction(connection):
            acted = _settle(connection, logged, acting, results, rescan)

    clean = (
        settings.apply
        and all(status == "done" for _, status, _ in acted)
        and not planned_now.waiting
        and not planned_now.deferred
        and not judgment.held
    )

    if clean:
        write_fingerprint(connection, platform.name, post.post_id, platform.fingerprint(post))

    return Moderation(
        post=post,
        scan=scan,
        judgment=judgment,
        plan=planned_now,
        acted=tuple(acted),
        dry_run=tuple(dry_run),
        clean=clean,
        results=tuple(results),
    )
