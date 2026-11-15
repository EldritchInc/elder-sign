"""Tests proving the tool documents are built from their sources, and stay built."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from azathoth.tools import ProcessToolExecutor, decode_tool_document, encode_tool_document

import tests.fixtures.echo_tool as echo_tool
from elder_sign.build import (
    BuildError,
    _shared_closure,
    _split_source,
    assemble_source,
    build_document,
    main,
    stale_documents,
    write_documents,
)

ECHO = ("tests.fixtures.echo_tool",)


# Assembling one script


def test_an_assembled_tool_runs_as_one_process() -> None:
    (implementation,) = build_document(echo_tool).implementations

    output = asyncio.run(ProcessToolExecutor().execute(implementation, {"message": "hi"}))

    assert output == {"echo": "HI"}


def test_an_assembled_tool_inlines_its_shared_modules_in_place_of_their_imports() -> None:
    source = assemble_source(echo_tool)

    assert "from elder_sign" not in source
    assert source.startswith('"""Shout a message back')
    assert source.count('"""Shout a message back') == 1
    assert source.index("# --- elder_sign.shared.protocol ---") < source.index(
        "# --- tests.fixtures.echo_tool ---"
    )
    assert source.endswith('if __name__ == "__main__":\n    main(run)\n')


def test_a_shared_module_brings_no_docstring_into_the_script() -> None:
    assert "How an Elder Sign tool talks to Azathoth" not in assemble_source(echo_tool)


# The document


def test_the_document_declares_one_process_implementation_of_the_declared_tool() -> None:
    document = build_document(echo_tool)
    (implementation,) = document.implementations

    assert document.definition.id == UUID(echo_tool.TOOL_ID)
    assert document.definition.name == "echo_shout"
    assert document.definition.input_schema.json_schema == echo_tool.INPUT_SCHEMA
    assert implementation.id == UUID(echo_tool.IMPLEMENTATION_ID)
    assert implementation.runtime == "process"
    assert implementation.command == ("python",)
    assert implementation.environment == ("ECHO_SETTING",)
    assert implementation.source == assemble_source(echo_tool)


def test_test_cases_keep_the_same_ids_from_build_to_build() -> None:
    (case,) = build_document(echo_tool).test_cases

    assert case.id == uuid5(UUID(echo_tool.TOOL_ID), "shouts")
    assert (case.inputs, case.expected_output) == ({"message": "hi"}, {"echo": "HI"})
    assert build_document(echo_tool) == build_document(echo_tool)


def test_a_written_document_is_canonical(
    tmp_path: Path,
) -> None:
    (path,) = write_documents(ECHO, tmp_path)
    encoded = path.read_text(encoding="utf-8")

    assert path.name == "echo-shout.json"
    assert encode_tool_document(decode_tool_document(encoded)) + "\n" == encoded


# Staying built


def test_written_documents_are_not_stale(
    tmp_path: Path,
) -> None:
    write_documents(ECHO, tmp_path)

    assert stale_documents(ECHO, tmp_path) == []
    assert write_documents(ECHO, tmp_path) == []


def test_a_missing_changed_or_orphaned_document_is_stale(
    tmp_path: Path,
) -> None:
    assert stale_documents(ECHO, tmp_path) == ["echo-shout.json is missing"]

    (path,) = write_documents(ECHO, tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("HI", "HO"), encoding="utf-8")
    (tmp_path / "forgotten.json").write_text("{}", encoding="utf-8")

    assert stale_documents(ECHO, tmp_path) == [
        "echo-shout.json is out of date with its source",
        "forgotten.json is built by no tool",
    ]


def test_the_shipped_documents_are_built_from_the_current_sources() -> None:
    assert stale_documents() == []
    assert main(["--check"]) == 0


# What cannot be inlined


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        ("from elder_sign.shared.protocol import main as entry\n", "with an alias"),
        ("import elder_sign.shared.protocol\n", "as a module"),
        ("from elder_sign.build import main\n", "only shared modules"),
        ("from __future__ import annotations\n", "__future__"),
        (
            "def run():\n    from elder_sign.shared.protocol import main\n",
            "below the top level",
        ),
    ],
    ids=["alias", "module import", "not shared", "future", "nested"],
)
def test_imports_that_cannot_be_inlined_are_refused(
    source: str,
    problem: str,
) -> None:
    with pytest.raises(BuildError, match=problem):
        _split_source("tool", source)


def test_a_shared_module_must_be_listed() -> None:
    with pytest.raises(BuildError, match="not listed in SHARED_MODULES"):
        _shared_closure(["elder_sign.shared.unlisted"])
