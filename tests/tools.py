"""Running the shipped tool documents in tests, as Azathoth runs them."""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from azathoth.tools import ProcessToolExecutor, decode_tool_document

from elder_sign.build import DOCUMENTS
from elder_sign.shared.store import DATABASE_ENVIRONMENT_VARIABLE


def run_tool(
    name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Run one shipped tool, by name, through Azathoth's process runtime."""

    path = DOCUMENTS / f"{name.replace('_', '-')}.json"
    (implementation,) = decode_tool_document(path.read_text(encoding="utf-8")).implementations

    return cast(dict[str, Any], asyncio.run(ProcessToolExecutor().execute(implementation, inputs)))


def grant_state_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Point every tool at a fresh state file for one test, and return it."""

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))

    return path
