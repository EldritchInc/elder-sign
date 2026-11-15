"""The log of every action Elder Sign plans or takes in her name, so nothing done is redone.

Each action is logged when it is planned, and settled `done` or `failed`
once it has been tried; a dry run is logged and never settled. The log keeps
which platform, post and comment, what was done, the verdict and the layer
that asked for it, and why an action failed: never a word of any comment,
so the log can be read without reading the trolls.

An action logged `done` on a comment is never planned again on it: `pending`
drops it from a plan. One that failed, or was only a dry run, is planned
again, so a run that breaks partway is finished by the next.

The log is the state file's `actions` table, which the schema has held since
its first version.
"""

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from elder_sign.planning import Action, Plan
from elder_sign.shared.store import recorded_now

SETTLED: Final = ("done", "failed")
"""How an action that was tried ends."""


@dataclass(frozen=True)
class Entry:
    """One action in the log."""

    id: int
    platform: str
    post_id: str
    comment_id: str
    action: str
    verdict: str | None
    layer: str | None
    dry_run: bool
    status: str
    detail: str | None
    recorded_at: str


def planned(
    connection: sqlite3.Connection,
    platform: str,
    post_id: str,
    action: Action,
    verdict: str | None,
) -> int:
    """Log one action as planned, and return its entry's ID."""

    cursor = connection.execute(
        """
        INSERT INTO actions (
            platform, post_id, comment_id, action, verdict, layer, dry_run, status, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)
        """,
        (
            platform,
            post_id,
            action.comment_id,
            action.action,
            verdict,
            action.layer,
            int(action.dry_run),
            recorded_now(),
        ),
    )

    return int(cursor.lastrowid or 0)


def settled(
    connection: sqlite3.Connection,
    entry_id: int,
    status: str,
    detail: str | None = None,
) -> None:
    """Settle one logged action as done or failed, saying why when it failed.

    Raises:
        ValueError: The status is not done or failed.
    """

    if status not in SETTLED:
        raise ValueError(f"An action is settled done or failed, not {status!r}.")

    connection.execute(
        "UPDATE actions SET status = ?, detail = ?, recorded_at = ? WHERE id = ?",
        (status, detail, recorded_now(), entry_id),
    )


def entries(
    connection: sqlite3.Connection,
    platform: str,
    *,
    post_id: str | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[Entry]:
    """Return the log for one platform, newest first, for one post or all, since a time."""

    rows = connection.execute(
        """
        SELECT id, platform, post_id, comment_id, action, verdict, layer, dry_run, status,
               detail, recorded_at
        FROM actions
        WHERE platform = ?
          AND (? IS NULL OR post_id = ?)
          AND (? IS NULL OR recorded_at >= ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            platform,
            post_id,
            post_id,
            None if since is None else since.replace(microsecond=0).isoformat(),
            None if since is None else since.replace(microsecond=0).isoformat(),
            limit,
        ),
    ).fetchall()

    return [
        Entry(
            id=int(row[0]),
            platform=str(row[1]),
            post_id=str(row[2]),
            comment_id=str(row[3]),
            action=str(row[4]),
            verdict=row[5],
            layer=row[6],
            dry_run=bool(row[7]),
            status=str(row[8]),
            detail=row[9],
            recorded_at=str(row[10]),
        )
        for row in rows
    ]


def pending(
    connection: sqlite3.Connection,
    platform: str,
    plan: Plan,
) -> Plan:
    """Return a plan without the actions already done on their comments."""

    done = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            """
            SELECT comment_id, action FROM actions
            WHERE platform = ? AND status = 'done' AND dry_run = 0
            """,
            (platform,),
        )
    }

    return replace(
        plan,
        actions=tuple(
            action for action in plan.actions if (action.comment_id, action.action) not in done
        ),
    )
