"""Elder Sign's state: one SQLite file every tool opens for itself.

A tool finds the file through `ELDER_SIGN_DB`, which its implementation is
granted (ADR 0099), or at `~/.elder-sign/state.db` when that is unset. The
file is created, and migrated to the current schema, the first time any tool
opens it. Writers wait for each other rather than fail, so tools a map runs
concurrently can share it.

The tables:

- `posts`: the fingerprint of each post's comments when it was last cleaned,
  so a scan skips a post whose comments have not changed;
- `verdicts`: what was decided about a comment's content, or about it in
  reply to its parent, under one policy;
- `authors`: the people who are trusted, and the people who are blocked;
- `terms`: the terms that make a comment hostile under one policy;
- `actions`: every hide, delete and restriction, planned or done;
- `media`: what each GIF or image shows, as a model described it once, so a
  comment's words are judged beside its media without looking at it again.
"""

import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

DATABASE_ENVIRONMENT_VARIABLE: Final = "ELDER_SIGN_DB"
"""The variable naming the state file, granted to every tool that opens it."""

SCHEMA_VERSION: Final = 2
"""The schema version this Elder Sign writes, kept in SQLite's `user_version`."""

BUSY_TIMEOUT_SECONDS: Final = 30.0
"""How long a writer waits for another to finish before it fails."""

VERDICTS: Final = ("friendly", "hostile", "uncertain")
"""What can be decided about a comment."""

KEY_KINDS: Final = ("content", "context")
"""A verdict about a comment's content wherever it appears, or about it in reply to its parent."""

VERDICT_SOURCES: Final = ("model", "human")
"""Who decided: a human's verdict is never replaced by a model's."""

STANDINGS: Final = ("trusted", "blocked")
"""An author's standing."""

ACTIONS: Final = ("hide", "unhide", "delete", "restrict")
"""What Elder Sign can do to a comment or its author."""

ACTION_STATUSES: Final = ("planned", "done", "failed")
"""Where an action stands."""

MIGRATIONS: Final = (
    """
    CREATE TABLE posts (
        platform TEXT NOT NULL,
        post_id TEXT NOT NULL,
        comment_count INTEGER NOT NULL CHECK (comment_count >= 0),
        newest_comment_id TEXT,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (platform, post_id)
    );

    CREATE TABLE verdicts (
        policy TEXT NOT NULL,
        key_kind TEXT NOT NULL CHECK (key_kind IN ('content', 'context')),
        key TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('friendly', 'hostile', 'uncertain')),
        source TEXT NOT NULL CHECK (source IN ('model', 'human')),
        model TEXT,
        reason TEXT,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (policy, key_kind, key)
    );

    CREATE TABLE authors (
        platform TEXT NOT NULL,
        author_id TEXT NOT NULL,
        standing TEXT NOT NULL CHECK (standing IN ('trusted', 'blocked')),
        note TEXT,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (platform, author_id)
    );

    CREATE TABLE terms (
        policy TEXT NOT NULL,
        term TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (policy, term)
    );

    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        post_id TEXT NOT NULL,
        comment_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('hide', 'unhide', 'delete', 'restrict')),
        verdict TEXT,
        layer TEXT,
        dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
        status TEXT NOT NULL CHECK (status IN ('planned', 'done', 'failed')),
        detail TEXT,
        recorded_at TEXT NOT NULL
    );

    CREATE INDEX actions_by_comment ON actions (platform, comment_id);
    """,
    """
    CREATE TABLE media (
        media_key TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        model TEXT,
        recorded_at TEXT NOT NULL
    );
    """,
)
"""The script that brings the schema from each version to the next; version N runs script N - 1."""


class StoreError(RuntimeError):
    """Raised when the state file cannot be used, or is asked to keep something invalid."""


@dataclass(frozen=True)
class Fingerprint:
    """What a post's comments looked like when it was last cleaned."""

    comment_count: int
    newest_comment_id: str | None


@dataclass(frozen=True)
class Verdict:
    """What was decided about one key under one policy, and by whom."""

    verdict: str
    source: str
    model: str | None
    reason: str | None


def database_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the state file: `ELDER_SIGN_DB`, or `~/.elder-sign/state.db`."""

    environment = os.environ if environment is None else environment
    configured = environment.get(DATABASE_ENVIRONMENT_VARIABLE, "").strip()

    if configured:
        return Path(configured).expanduser()

    return Path.home() / ".elder-sign" / "state.db"


def recorded_now() -> str:
    """Return the current time as it is recorded: UTC, ISO 8601, to the second."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _require(
    value: str,
    allowed: tuple[str, ...],
    what: str,
) -> None:
    """Refuse a value outside one of the schema's vocabularies."""

    if value not in allowed:
        raise StoreError(f"{what} {value!r} is not one of {', '.join(allowed)}.")


def migrate(
    connection: sqlite3.Connection,
) -> None:
    """Bring the state file to the current schema, one version at a time.

    Raises:
        StoreError: The file was written by a newer Elder Sign.
    """

    (version,) = connection.execute("PRAGMA user_version").fetchone()

    if version > SCHEMA_VERSION:
        raise StoreError(
            f"The state file has schema version {version}, newer than this Elder Sign's "
            f"{SCHEMA_VERSION}; upgrade Elder Sign to use it."
        )

    while version < SCHEMA_VERSION:
        connection.execute("BEGIN IMMEDIATE")

        try:
            (current,) = connection.execute("PRAGMA user_version").fetchone()

            if current == version:
                for statement in MIGRATIONS[version].split(";"):
                    if statement.strip():
                        connection.execute(statement)

                connection.execute(f"PRAGMA user_version = {version + 1}")

            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

        (version,) = connection.execute("PRAGMA user_version").fetchone()


def connect(
    path: Path | None = None,
) -> sqlite3.Connection:
    """Open the state file, creating and migrating it as needed.

    The connection is in autocommit mode: each write stands on its own, and a
    group of writes that must stand together is wrapped in `transaction`.
    """

    path = database_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)

    try:
        connection.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        migrate(connection)
    except BaseException:
        connection.close()
        raise

    return connection


@contextmanager
def transaction(
    connection: sqlite3.Connection,
) -> Iterator[sqlite3.Connection]:
    """Run a group of writes as one: all of them stand, or none do."""

    connection.execute("BEGIN IMMEDIATE")

    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise

    connection.execute("COMMIT")


def read_fingerprint(
    connection: sqlite3.Connection,
    platform: str,
    post_id: str,
) -> Fingerprint | None:
    """Return a post's fingerprint from when it was last cleaned, if it ever was."""

    row = connection.execute(
        "SELECT comment_count, newest_comment_id FROM posts WHERE platform = ? AND post_id = ?",
        (platform, post_id),
    ).fetchone()

    return None if row is None else Fingerprint(comment_count=row[0], newest_comment_id=row[1])


def write_fingerprint(
    connection: sqlite3.Connection,
    platform: str,
    post_id: str,
    fingerprint: Fingerprint,
) -> None:
    """Keep a post's fingerprint, replacing the one before."""

    if fingerprint.comment_count < 0:
        raise StoreError(f"A post cannot have {fingerprint.comment_count} comments.")

    connection.execute(
        """
        INSERT INTO posts (platform, post_id, comment_count, newest_comment_id, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (platform, post_id) DO UPDATE SET
            comment_count = excluded.comment_count,
            newest_comment_id = excluded.newest_comment_id,
            recorded_at = excluded.recorded_at
        """,
        (
            platform,
            post_id,
            fingerprint.comment_count,
            fingerprint.newest_comment_id,
            recorded_now(),
        ),
    )


def read_verdict(
    connection: sqlite3.Connection,
    policy: str,
    key_kind: str,
    key: str,
) -> Verdict | None:
    """Return what was decided about one key under one policy, if anything was."""

    _require(key_kind, KEY_KINDS, "Key kind")

    row = connection.execute(
        """
        SELECT verdict, source, model, reason FROM verdicts
        WHERE policy = ? AND key_kind = ? AND key = ?
        """,
        (policy, key_kind, key),
    ).fetchone()

    return (
        None if row is None else Verdict(verdict=row[0], source=row[1], model=row[2], reason=row[3])
    )


def write_verdict(
    connection: sqlite3.Connection,
    policy: str,
    key_kind: str,
    key: str,
    verdict: Verdict,
) -> bool:
    """Keep what was decided about one key, unless a human decided it and this is a model.

    Writing the same verdict again changes nothing, so a resumed run may
    repeat its writes. Returns whether the verdict was kept.
    """

    _require(key_kind, KEY_KINDS, "Key kind")
    _require(verdict.verdict, VERDICTS, "Verdict")
    _require(verdict.source, VERDICT_SOURCES, "Verdict source")

    cursor = connection.execute(
        """
        INSERT INTO verdicts (policy, key_kind, key, verdict, source, model, reason, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (policy, key_kind, key) DO UPDATE SET
            verdict = excluded.verdict,
            source = excluded.source,
            model = excluded.model,
            reason = excluded.reason,
            recorded_at = excluded.recorded_at
        WHERE verdicts.source = 'model' OR excluded.source = 'human'
        """,
        (
            policy,
            key_kind,
            key,
            verdict.verdict,
            verdict.source,
            verdict.model,
            verdict.reason,
            recorded_now(),
        ),
    )

    return cursor.rowcount == 1


def read_standing(
    connection: sqlite3.Connection,
    platform: str,
    author_id: str,
) -> str | None:
    """Return whether an author is trusted or blocked, if either."""

    row = connection.execute(
        "SELECT standing FROM authors WHERE platform = ? AND author_id = ?",
        (platform, author_id),
    ).fetchone()

    return None if row is None else str(row[0])


def write_standing(
    connection: sqlite3.Connection,
    platform: str,
    author_id: str,
    standing: str,
    note: str | None = None,
) -> None:
    """Trust or block an author, replacing their standing before."""

    _require(standing, STANDINGS, "Standing")

    connection.execute(
        """
        INSERT INTO authors (platform, author_id, standing, note, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (platform, author_id) DO UPDATE SET
            standing = excluded.standing,
            note = excluded.note,
            recorded_at = excluded.recorded_at
        """,
        (platform, author_id, standing, note, recorded_now()),
    )


def forget_standing(
    connection: sqlite3.Connection,
    platform: str,
    author_id: str,
) -> None:
    """Make an author neither trusted nor blocked."""

    connection.execute(
        "DELETE FROM authors WHERE platform = ? AND author_id = ?",
        (platform, author_id),
    )


def read_terms(
    connection: sqlite3.Connection,
    policy: str,
) -> tuple[str, ...]:
    """Return the terms that make a comment hostile under one policy, in order."""

    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT term FROM terms WHERE policy = ? ORDER BY term",
            (policy,),
        )
    )


def write_term(
    connection: sqlite3.Connection,
    policy: str,
    term: str,
) -> None:
    """Add a term that makes a comment hostile under one policy, as it was written."""

    if not term.strip():
        raise StoreError("A blocked term cannot be blank.")

    connection.execute(
        "INSERT OR IGNORE INTO terms (policy, term, recorded_at) VALUES (?, ?, ?)",
        (policy, term.strip(), recorded_now()),
    )


def forget_term(
    connection: sqlite3.Connection,
    policy: str,
    term: str,
) -> None:
    """Remove a term from a policy."""

    connection.execute(
        "DELETE FROM terms WHERE policy = ? AND term = ?",
        (policy, term.strip()),
    )


def read_description(
    connection: sqlite3.Connection,
    media_key: str,
) -> str | None:
    """Return what one GIF or image shows, if it was ever described."""

    row = connection.execute(
        "SELECT description FROM media WHERE media_key = ?",
        (media_key,),
    ).fetchone()

    return None if row is None else str(row[0])


def write_description(
    connection: sqlite3.Connection,
    media_key: str,
    description: str,
    model: str | None = None,
) -> None:
    """Keep what one GIF or image shows, replacing what was kept before."""

    if not description.strip():
        raise StoreError("A media description cannot be blank.")

    connection.execute(
        """
        INSERT INTO media (media_key, description, model, recorded_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (media_key) DO UPDATE SET
            description = excluded.description,
            model = excluded.model,
            recorded_at = excluded.recorded_at
        """,
        (media_key, description.strip(), model, recorded_now()),
    )
