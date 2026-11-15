"""Tests every shipped tool document must pass: canonical, distinct, and true to its test cases."""

import asyncio
from pathlib import Path

import pytest
from azathoth.tools import ProcessToolExecutor, decode_tool_document, encode_tool_document

from elder_sign.build import DOCUMENTS
from elder_sign.tools import TOOL_MODULES

SHIPPED = sorted(DOCUMENTS.glob("*.json"))


def test_every_tool_module_ships_a_document() -> None:
    assert len(SHIPPED) == len(TOOL_MODULES)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_the_document_is_canonical(
    path: Path,
) -> None:
    encoded = path.read_text(encoding="utf-8")

    assert encode_tool_document(decode_tool_document(encoded)) + "\n" == encoded


RELEASED: dict[str, str] = {
    "e1de7516-0000-4000-8000-000000000011": "normalize_comments@1.0.0",
    "e1de7516-0000-4000-8000-000000000041": "lookup_verdicts@1.0.0",
    "e1de7516-0000-4000-8000-000000000042": "lookup_verdicts@1.1.0",
    "e1de7516-0000-4000-8000-000000000071": "prepare_classification@1.0.0",
}
"""Implementation IDs that earlier versions of a tool were released with.

Azathoth refuses a document whose implementation ID belongs to another
version of its tool, so a new version must have a new implementation ID:
add the old one here when a tool's version changes.
"""


def test_a_new_version_of_a_tool_never_reuses_an_earlier_versions_implementation() -> None:
    for path in SHIPPED:
        document = decode_tool_document(path.read_text(encoding="utf-8"))
        current = f"{document.definition.name}@{document.definition.version}"

        for implementation in document.implementations:
            released = RELEASED.get(str(implementation.id))

            assert released in (None, current), (
                f"{current} reuses the implementation ID {released} was released with."
            )


def test_every_shipped_tool_imports_into_an_azathoth_holding_its_earlier_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    from azathoth.cli import DATABASE_ENVIRONMENT_VARIABLE as AZATHOTH_DATABASE
    from azathoth.cli import main

    monkeypatch.setenv(AZATHOTH_DATABASE, str(tmp_path / "azathoth.db"))
    assert main(["init"]) == 0

    for path in SHIPPED:
        encoded = json.loads(path.read_text(encoding="utf-8"))

        for implementation_id, released in RELEASED.items():
            name, version = released.split("@")

            if encoded["definition"]["name"] != name:
                continue

            earlier = json.loads(json.dumps(encoded))
            earlier["definition"]["version"] = version
            earlier["implementations"][0]["id"] = implementation_id
            earlier["implementations"][0]["tool_version"] = version
            old = tmp_path / f"{name}-{version}.json"
            old.write_text(json.dumps(earlier))

            assert main(["tool", "import", str(old)]) == 0, released

        assert main(["tool", "import", str(path)]) == 0, path.name


def test_no_two_documents_share_an_id_or_a_name() -> None:
    documents = [decode_tool_document(path.read_text(encoding="utf-8")) for path in SHIPPED]
    ids = [document.definition.id for document in documents] + [
        implementation.id for document in documents for implementation in document.implementations
    ]
    names = [document.definition.name for document in documents]

    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda path: path.name)
def test_the_document_passes_its_own_test_cases(
    path: Path,
) -> None:
    document = decode_tool_document(path.read_text(encoding="utf-8"))
    (implementation,) = document.implementations

    for case in document.test_cases:
        output = asyncio.run(ProcessToolExecutor().execute(implementation, dict(case.inputs)))

        assert output == case.expected_output, case.name
