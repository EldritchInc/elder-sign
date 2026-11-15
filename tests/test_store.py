"""Tests proving the state file: created and migrated on first use, and what it keeps."""

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from azathoth.tools import ProcessToolExecutor

import tests.fixtures.store_probe_tool as store_probe_tool
from elder_sign.build import (
    _module_source,
    _shared_closure,
    _split_source,
    assemble_source,
    build_document,
)
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    MIGRATIONS,
    SCHEMA_VERSION,
    Fingerprint,
    StoreError,
    Verdict,
    connect,
    database_path,
    forget_standing,
    forget_term,
    read_description,
    read_fingerprint,
    read_standing,
    read_terms,
    read_verdict,
    transaction,
    write_description,
    write_fingerprint,
    write_standing,
    write_term,
    write_verdict,
)

TABLES = {"actions", "authors", "media", "posts", "terms", "verdicts"}

MODEL_HOSTILE = Verdict(verdict="hostile", source="model", model="m", reason="slur")

MODEL_FRIENDLY = Verdict(verdict="friendly", source="model", model="m", reason=None)

HUMAN_FRIENDLY = Verdict(verdict="friendly", source="human", model=None, reason="my friend")


@pytest.fixture
def state(
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open a fresh state file for one test."""

    connection = connect(tmp_path / "state.db")

    try:
        yield connection
    finally:
        connection.close()


def tables(
    connection: sqlite3.Connection,
) -> set[str]:
    """Return the names of the tables in a state file."""

    return {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    }


# Finding and creating the file


def test_the_file_is_named_by_its_variable_or_lives_in_the_home_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert database_path({}) == tmp_path / ".elder-sign" / "state.db"
    assert (
        database_path({DATABASE_ENVIRONMENT_VARIABLE: "  "})
        == tmp_path / ".elder-sign" / "state.db"
    )
    assert database_path({DATABASE_ENVIRONMENT_VARIABLE: "/x/y.db"}) == Path("/x/y.db")


def test_opening_creates_the_file_and_its_folders_at_the_current_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deep" / "folder" / "state.db"
    connection = connect(path)

    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
        (mode,) = connection.execute("PRAGMA journal_mode").fetchone()

        assert path.exists()
        assert version == SCHEMA_VERSION
        assert mode == "wal"
        assert tables(connection) == TABLES
    finally:
        connection.close()


def test_reopening_keeps_what_was_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"

    for _ in range(2):
        connection = connect(path)
        write_term(connection, "transphobia", "slur")
        connection.close()

    connection = connect(path)

    try:
        assert read_terms(connection, "transphobia") == ("slur",)
    finally:
        connection.close()


def test_a_file_from_a_newer_elder_sign_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.close()

    with pytest.raises(StoreError, match="newer than this Elder Sign"):
        connect(path)


def test_a_file_at_an_older_schema_is_migrated_and_keeps_what_it_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    raw = sqlite3.connect(path, isolation_level=None)

    for statement in MIGRATIONS[0].split(";"):
        if statement.strip():
            raw.execute(statement)

    raw.execute("PRAGMA user_version = 1")
    raw.execute(
        "INSERT INTO terms (policy, term, recorded_at) VALUES ('transphobia', 'slur', 'then')"
    )
    raw.close()

    connection = connect(path)

    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()

        assert version == SCHEMA_VERSION == 2
        assert tables(connection) == TABLES
        assert read_terms(connection, "transphobia") == ("slur",)
    finally:
        connection.close()


def test_every_schema_version_has_its_migration() -> None:
    assert len(MIGRATIONS) == SCHEMA_VERSION


# Fingerprints


def test_a_fingerprint_is_kept_and_replaced(
    state: sqlite3.Connection,
) -> None:
    assert read_fingerprint(state, "facebook", "reel-1") is None

    write_fingerprint(state, "facebook", "reel-1", Fingerprint(3, "c3"))
    write_fingerprint(state, "facebook", "reel-1", Fingerprint(5, "c5"))
    write_fingerprint(state, "instagram", "reel-1", Fingerprint(0, None))

    assert read_fingerprint(state, "facebook", "reel-1") == Fingerprint(5, "c5")
    assert read_fingerprint(state, "instagram", "reel-1") == Fingerprint(0, None)


def test_a_negative_comment_count_is_refused(
    state: sqlite3.Connection,
) -> None:
    with pytest.raises(StoreError):
        write_fingerprint(state, "facebook", "reel-1", Fingerprint(-1, None))


# Verdicts


def test_a_verdict_is_kept_by_policy_kind_and_key(
    state: sqlite3.Connection,
) -> None:
    assert write_verdict(state, "transphobia", "content", "t:abc", MODEL_HOSTILE)

    assert read_verdict(state, "transphobia", "content", "t:abc") == MODEL_HOSTILE
    assert read_verdict(state, "transphobia", "context", "t:abc") is None
    assert read_verdict(state, "racism", "content", "t:abc") is None


def test_writing_the_same_verdict_again_changes_nothing(
    state: sqlite3.Connection,
) -> None:
    write_verdict(state, "transphobia", "content", "t:abc", MODEL_HOSTILE)
    write_verdict(state, "transphobia", "content", "t:abc", MODEL_HOSTILE)

    (count,) = state.execute("SELECT count(*) FROM verdicts").fetchone()

    assert count == 1
    assert read_verdict(state, "transphobia", "content", "t:abc") == MODEL_HOSTILE


def test_a_model_never_replaces_a_human_but_a_human_replaces_a_model(
    state: sqlite3.Connection,
) -> None:
    write_verdict(state, "transphobia", "content", "t:abc", MODEL_HOSTILE)

    assert write_verdict(state, "transphobia", "content", "t:abc", HUMAN_FRIENDLY)
    assert not write_verdict(state, "transphobia", "content", "t:abc", MODEL_HOSTILE)
    assert read_verdict(state, "transphobia", "content", "t:abc") == HUMAN_FRIENDLY

    write_verdict(state, "transphobia", "content", "t:def", MODEL_HOSTILE)

    assert write_verdict(state, "transphobia", "content", "t:def", MODEL_FRIENDLY)
    assert read_verdict(state, "transphobia", "content", "t:def") == MODEL_FRIENDLY


@pytest.mark.parametrize(
    ("kind", "verdict"),
    [
        ("thread", MODEL_HOSTILE),
        ("content", Verdict(verdict="spicy", source="model", model=None, reason=None)),
        ("content", Verdict(verdict="hostile", source="oracle", model=None, reason=None)),
    ],
    ids=["kind", "verdict", "source"],
)
def test_a_verdict_outside_the_vocabulary_is_refused(
    state: sqlite3.Connection,
    kind: str,
    verdict: Verdict,
) -> None:
    with pytest.raises(StoreError):
        write_verdict(state, "transphobia", kind, "t:abc", verdict)


# Authors and terms


def test_an_author_is_trusted_blocked_and_forgotten(
    state: sqlite3.Connection,
) -> None:
    assert read_standing(state, "facebook", "u1") is None

    write_standing(state, "facebook", "u1", "trusted", "bestie")
    assert read_standing(state, "facebook", "u1") == "trusted"
    assert read_standing(state, "instagram", "u1") is None

    write_standing(state, "facebook", "u1", "blocked")
    assert read_standing(state, "facebook", "u1") == "blocked"

    forget_standing(state, "facebook", "u1")
    assert read_standing(state, "facebook", "u1") is None

    with pytest.raises(StoreError):
        write_standing(state, "facebook", "u1", "frenemy")


def test_terms_are_kept_per_policy_once_each(
    state: sqlite3.Connection,
) -> None:
    write_term(state, "transphobia", " zeta ")
    write_term(state, "transphobia", "alpha")
    write_term(state, "transphobia", "alpha")
    write_term(state, "racism", "other")

    assert read_terms(state, "transphobia") == ("alpha", "zeta")

    forget_term(state, "transphobia", "zeta")
    assert read_terms(state, "transphobia") == ("alpha",)

    with pytest.raises(StoreError):
        write_term(state, "transphobia", "   ")


# Media descriptions


def test_a_description_is_kept_and_replaced(
    state: sqlite3.Connection,
) -> None:
    assert read_description(state, "m:giphy:abc") is None

    write_description(state, "m:giphy:abc", "  a clown honking a horn ", "vision-model")
    assert read_description(state, "m:giphy:abc") == "a clown honking a horn"

    write_description(state, "m:giphy:abc", "a clown honking a red horn")
    assert read_description(state, "m:giphy:abc") == "a clown honking a red horn"
    assert read_description(state, "m:giphy:other") is None


def test_a_blank_description_is_refused(
    state: sqlite3.Connection,
) -> None:
    with pytest.raises(StoreError):
        write_description(state, "m:giphy:abc", "   ")


# Writing together


def test_a_transaction_keeps_all_its_writes_or_none(
    state: sqlite3.Connection,
) -> None:
    with transaction(state):
        write_term(state, "transphobia", "one")
        write_term(state, "transphobia", "two")

    with pytest.raises(StoreError), transaction(state):
        write_term(state, "transphobia", "three")
        write_term(state, "transphobia", " ")

    assert read_terms(state, "transphobia") == ("one", "two")


def test_two_connections_write_the_same_file(
    tmp_path: Path,
) -> None:
    first = connect(tmp_path / "state.db")
    second = connect(tmp_path / "state.db")

    try:
        with transaction(first):
            write_term(first, "transphobia", "one")

        write_term(second, "transphobia", "two")

        assert read_terms(first, "transphobia") == ("one", "two")
    finally:
        first.close()
        second.close()


# Inlined into a tool


def test_the_store_imports_no_other_shared_module() -> None:
    _, imported, _ = _split_source(
        "elder_sign.shared.store", _module_source("elder_sign.shared.store")
    )

    assert imported == ()
    assert _shared_closure(["elder_sign.shared.store"]) == (
        "elder_sign.shared.protocol",
        "elder_sign.shared.store",
    )


def test_a_tool_inlining_the_store_keeps_state_in_the_file_it_is_granted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "granted.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    (implementation,) = build_document(store_probe_tool).implementations

    output = asyncio.run(
        ProcessToolExecutor().execute(implementation, {"post_id": "reel-9", "comment_count": 4})
    )

    connection = connect(path)

    try:
        assert output == {"kept": True}
        assert read_fingerprint(connection, "test", "reel-9") == Fingerprint(4, None)
        assert "# --- elder_sign.shared.store ---" in assemble_source(store_probe_tool)
    finally:
        connection.close()
