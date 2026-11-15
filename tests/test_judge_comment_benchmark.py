"""Tests proving the judge-comment benchmark measures exactly the judge a scan uses."""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from azathoth.evaluation import decode_benchmark_document, encode_benchmark_document
from azathoth.workflows import decode_workflow_document

from elder_sign.build import DOCUMENTS
from tests.test_classify_comment import MODELS
from tests.test_media_judging import cli, install
from tests.tools import run_tool

DOCUMENTS_ROOT = DOCUMENTS.parent

BENCHMARK = DOCUMENTS_ROOT / "benchmarks" / "judge-comment.json"

BENCHMARK_ID = "e1de7516-0000-4000-8000-000000000500"

JUDGE_COMMENT = DOCUMENTS_ROOT / "workflows" / "judge-comment.json"

JUDGE_COMMENT_ID = "e1de7516-0000-4000-8000-000000000310"

CLASSIFY_COMMENT = DOCUMENTS_ROOT / "workflows" / "classify-comment.json"


def cases() -> list[dict[str, Any]]:
    """Return the benchmark's cases as JSON."""

    dataset = decode_benchmark_document(BENCHMARK.read_text(encoding="utf-8"))
    found: list[dict[str, Any]] = [case.model_dump(mode="json") for case in dataset.cases]

    return found


def expected() -> Counter[str]:
    """Return how many cases expect each verdict."""

    return Counter(case["expected"]["value"] for case in cases())


# The benchmark


def test_the_benchmark_is_canonical() -> None:
    encoded = BENCHMARK.read_text(encoding="utf-8")

    assert encode_benchmark_document(decode_benchmark_document(encoded)) + "\n" == encoded


def test_every_case_is_exact_on_one_verdict() -> None:
    assert len(cases()) == 51
    assert {case["expected"]["comparison"] for case in cases()} == {"exact"}
    assert expected() == {"hostile": 27, "friendly": 22, "uncertain": 2}


def test_the_policy_is_covered_area_by_area() -> None:
    areas = Counter(case["metadata"]["area"] for case in cases())

    assert set(areas) == {
        "womanhood",
        "dogwhistle",
        "transphobia",
        "misgendering",
        "appearance",
        "sanity",
        "injection",
        "media",
        "context",
        "support",
        "criticism",
        "neutral",
        "uncertain",
        "ally",
        "failure",
        "reaction",
    }
    assert areas["criticism"] >= 5
    assert areas["ally"] >= 2


def test_the_same_reply_is_judged_by_what_it_replies_to() -> None:
    by_text: dict[str, set[str]] = {}

    for case in cases():
        item = case["input"]["item"]

        if item["parent"] is not None:
            by_text.setdefault(item["comment"]["text"], set()).add(case["expected"]["value"])

    for reply in ("lol exactly", "this 👆", "😂😂😂"):
        assert by_text[reply] == {"hostile", "friendly"}, reply


def test_cases_show_what_her_post_says_and_what_she_wrote_herself() -> None:
    items = [case["input"]["item"] for case in cases()]

    assert sum(1 for item in items if item.get("caption")) >= 5
    assert sum(1 for item in items if (item["parent"] or {}).get("by_her")) >= 2


def test_every_case_is_an_item_as_a_map_gives_one() -> None:
    for case in cases():
        prepared = run_tool("prepare_classification", {"request": case["input"]})

        assert prepared["group"] == case["input"]["item"]


# The workflow it measures


def test_judge_comment_judges_with_classify_comments_very_strategy() -> None:
    judge = decode_workflow_document(JUDGE_COMMENT.read_text(encoding="utf-8"))
    classify = decode_workflow_document(CLASSIFY_COMMENT.read_text(encoding="utf-8"))

    assert [step.specification for step in judge.steps] == [
        step.specification for step in classify.steps
    ]
    assert [step.inputs for step in judge.steps] == [step.inputs for step in classify.steps]
    assert judge.result is not None
    assert judge.result.model_dump(mode="json") == {"name": "verdict"}


def passed(
    output: str,
) -> tuple[int, int]:
    """Return how many cases a benchmark run ran, and how many passed."""

    run = re.search(r"Cases Run: (\d+)", output)
    passing = re.search(r"Cases Passed: (\d+)", output)

    assert run is not None and passing is not None, output

    return int(run.group(1)), int(passing.group(1))


@pytest.mark.parametrize("verdict", ["hostile", "friendly", "uncertain"])
def test_a_judge_that_always_says_one_verdict_passes_only_those_cases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    verdict: str,
) -> None:
    install(monkeypatch, tmp_path, capsys, models=MODELS, reply=json.dumps({"verdict": verdict}))
    cli(capsys, "benchmark", "import", str(BENCHMARK))

    output = cli(capsys, "benchmark", "run", BENCHMARK_ID, "--workflow", JUDGE_COMMENT_ID)

    assert passed(output) == (51, expected()[verdict])
