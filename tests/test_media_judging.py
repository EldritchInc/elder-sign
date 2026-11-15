"""Tests proving a GIF or image is looked at once, by a model that can see, and remembered."""

import json
import sqlite3
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import azathoth.cli.bootstrap as cli_bootstrap
import pytest
from azathoth.cli import DATABASE_ENVIRONMENT_VARIABLE as AZATHOTH_DATABASE
from azathoth.cli import main
from azathoth.prompting import ContextPromptStrategySpec
from azathoth.providers import (
    DeterministicLanguageModel,
    LanguageModelRegistry,
    ModelCatalog,
    ModelMetadata,
    ModelModality,
)
from azathoth.workflows import decode_workflow_document

from elder_sign.build import DOCUMENTS
from elder_sign.policy import TRANSPHOBIC_HARASSMENT, TRANSPHOBIC_HARASSMENT_DEFINITION
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    read_description,
    read_verdict,
)
from tests.tools import run_tool

WORKFLOWS = DOCUMENTS.parent / "workflows"

JUDGE_MEDIA = WORKFLOWS / "judge-media.json"

JUDGE_MEDIA_ID = "e1de7516-0000-4000-8000-000000000200"

RECORD_MEDIA_ID = "e1de7516-0000-4000-8000-000000000210"

MEDIA_KEY = "m:giphy:3o7TKSjRrfIPjeiVyM"

VISION = ModelMetadata(
    provider="scripted",
    model="vision",
    display_name="Scripted Vision",
    input_modalities=frozenset({ModelModality.TEXT, ModelModality.IMAGE}),
    context_window_tokens=32_768,
)

BLIND = ModelMetadata(
    provider="scripted",
    model="blind",
    display_name="Scripted Text Only",
    context_window_tokens=32_768,
)

JUDGMENT = {
    "description": "A cartoon clown honking a horn, captioned 'nice try, sir'.",
    "hostile": True,
    "reason": "misgenders her",
}


def png() -> bytes:
    """Return a valid one-pixel PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def cli(
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> str:
    """Run one azathoth command that must succeed, returning what it printed."""

    status = main(arguments)
    captured = capsys.readouterr()

    assert status == 0, captured.err

    return captured.out


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give the tools a fresh state file, and the test a connection to it."""

    path = tmp_path / "elder-sign.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)

    try:
        yield connection
    finally:
        connection.close()


def install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    models: tuple[ModelMetadata, ...],
    reply: Any = JUDGMENT,
    replies: dict[str, Any] | None = None,
) -> None:
    """Install Elder Sign into a fresh Azathoth whose only models reply as scripted.

    Every model gives `reply`, but those named in `replies`, by identifier.
    """

    monkeypatch.setenv(AZATHOTH_DATABASE, str(tmp_path / "azathoth.db"))
    monkeypatch.delenv("AZATHOTH_CONTENT_STORE", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        cli_bootstrap, "_load_current_models", lambda _configuration: ModelCatalog(models=models)
    )
    scripted = models

    def text(answer: Any) -> str:
        return answer if isinstance(answer, str) else json.dumps(answer)

    answers = {
        model.identifier: text((replies or {}).get(model.identifier, reply)) for model in models
    }
    monkeypatch.setattr(
        cli_bootstrap,
        "_load_language_models",
        lambda *, configuration, models: LanguageModelRegistry(
            {
                model.identifier: DeterministicLanguageModel(
                    provider=model.provider,
                    model=model.model,
                    response_text=answers[model.identifier],
                )
                for model in scripted
            }
        ),
    )

    cli(capsys, "init")

    for model in models:
        cli(capsys, "model", "authorize", model.identifier)

    for path in sorted(DOCUMENTS.glob("*.json")):
        cli(capsys, "tool", "import", str(path))

    for path in sorted(WORKFLOWS.glob("*.json")):
        cli(capsys, "workflow", "import", str(path))


def judge(
    capsys: pytest.CaptureFixture[str],
    image: Path,
) -> str:
    """Run judge-media on one image file, returning what it printed."""

    return cli(
        capsys, "workflow", "run", JUDGE_MEDIA_ID, "--input-file", str(image), "--output", "result"
    )


def record(
    judgment: Any,
    *,
    media_key: str = MEDIA_KEY,
) -> dict[str, Any]:
    """Run the built record_media on one judgment."""

    return run_tool(
        "record_media",
        {
            "media": {"media_key": media_key, "judgment": judgment, "model": "scripted/vision"},
            "settings": {"policy": TRANSPHOBIC_HARASSMENT},
        },
    )


# judge-media


def test_judge_media_shows_the_image_to_a_model_that_can_see_and_names_none() -> None:
    workflow = decode_workflow_document(JUDGE_MEDIA.read_text(encoding="utf-8"))
    (step,) = workflow.steps
    specification = step.specification

    assert isinstance(specification, ContextPromptStrategySpec)

    requirements = specification.model_dump(mode="json")["model_selection"]["requirements"]

    assert set(requirements["required_input_modalities"]) == {"text", "image"}
    assert "{image}" in specification.template.text
    assert TRANSPHOBIC_HARASSMENT_DEFINITION.replace("{", "{{") in specification.template.text


def test_an_image_is_shown_to_the_model_and_its_judgment_returned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(monkeypatch, tmp_path, capsys, models=(VISION,))
    image = tmp_path / "frame.png"
    image.write_bytes(png())

    output = cli(
        capsys, "workflow", "run", JUDGE_MEDIA_ID, "--input-file", str(image), "--output", "result"
    )

    assert json.loads(output) == JUDGMENT


def test_an_image_is_never_given_to_a_model_that_cannot_see_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(monkeypatch, tmp_path, capsys, models=(BLIND,))
    image = tmp_path / "frame.png"
    image.write_bytes(png())

    status = main(
        ("workflow", "run", JUDGE_MEDIA_ID, "--input-file", str(image), "--output", "result")
    )
    captured = capsys.readouterr()

    assert status != 0
    assert "No executable context prompt candidate" in captured.err


# record-media


def test_a_hostile_judgment_keeps_the_description_and_a_hostile_verdict(
    state: sqlite3.Connection,
) -> None:
    assert record(JUDGMENT) == {"media_key": MEDIA_KEY, "described": True, "hostile": True}

    verdict = read_verdict(state, TRANSPHOBIC_HARASSMENT, "content", MEDIA_KEY)

    assert read_description(state, MEDIA_KEY) == JUDGMENT["description"]
    assert verdict is not None
    assert (verdict.verdict, verdict.source, verdict.model, verdict.reason) == (
        "hostile",
        "model",
        "scripted/vision",
        "misgenders her",
    )


def test_a_harmless_judgment_keeps_only_the_description(
    state: sqlite3.Connection,
) -> None:
    output = record({"description": "A cat waving.", "hostile": False})

    assert output == {"media_key": MEDIA_KEY, "described": True, "hostile": False}
    assert read_description(state, MEDIA_KEY) == "A cat waving."
    assert read_verdict(state, TRANSPHOBIC_HARASSMENT, "content", MEDIA_KEY) is None


@pytest.mark.parametrize(
    "judgment",
    [
        {"description": "A clown.", "hostile": "true"},
        {"description": "A clown.", "hostile": 1},
        {"description": "A clown."},
        "hostile",
        None,
    ],
    ids=["string", "number", "missing", "not an object", "nothing"],
)
def test_anything_but_true_is_not_hostile(
    state: sqlite3.Connection,
    judgment: Any,
) -> None:
    assert record(judgment)["hostile"] is False
    assert read_verdict(state, TRANSPHOBIC_HARASSMENT, "content", MEDIA_KEY) is None


@pytest.mark.parametrize(
    "description",
    [None, "", "   ", 42],
    ids=["missing", "empty", "blank", "number"],
)
def test_a_missing_description_is_not_kept_so_the_media_is_described_again(
    state: sqlite3.Connection,
    description: Any,
) -> None:
    output = record({"description": description, "hostile": True})

    assert output["described"] is False
    assert output["hostile"] is True
    assert read_description(state, MEDIA_KEY) is None


def test_a_long_description_is_cut_short(
    state: sqlite3.Connection,
) -> None:
    record({"description": "clown " * 200, "hostile": False})

    assert len(read_description(state, MEDIA_KEY) or "") == 500


def test_record_media_runs_as_a_workflow_with_no_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    state: sqlite3.Connection,
) -> None:
    install(monkeypatch, tmp_path, capsys, models=())
    media = {"media_key": MEDIA_KEY, "judgment": JUDGMENT, "model": "scripted/vision"}

    output = cli(
        capsys,
        "workflow",
        "run",
        RECORD_MEDIA_ID,
        "--input",
        json.dumps(media),
        "--output",
        "result",
    )

    assert json.loads(output) == {"media_key": MEDIA_KEY, "described": True, "hostile": True}
    assert read_description(state, MEDIA_KEY) == JUDGMENT["description"]


def test_judged_media_then_decides_a_comment_without_a_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    state: sqlite3.Connection,
) -> None:
    install(monkeypatch, tmp_path, capsys, models=(VISION,))
    image = tmp_path / "frame.png"
    image.write_bytes(png())

    judgment = json.loads(
        cli(
            capsys,
            "workflow",
            "run",
            JUDGE_MEDIA_ID,
            "--input-file",
            str(image),
            "--output",
            "result",
        )
    )
    record({**judgment})

    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "comments": [
            {
                "id": "c1",
                "author_id": "troll",
                "text": "lmao",
                "media": [
                    {
                        "kind": "gif",
                        "url": "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif",
                    }
                ],
            }
        ],
    }
    keyed = run_tool("normalize_comments", {"scan": scan})["scan"]
    resolution = run_tool(
        "lookup_verdicts", {"scan": keyed, "settings": {"policy": TRANSPHOBIC_HARASSMENT}}
    )["resolution"]

    assert resolution["needs_inference"] == []
    assert resolution["resolved"][0]["layer"] == "media"
