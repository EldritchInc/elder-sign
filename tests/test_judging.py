"""Tests proving a scan is judged: its new media described first, every comment given a verdict.

The steps are proven first with a scripted runner, which answers each
workflow as told and records what it was asked. Then the whole of judging
runs through Azathoth itself: a scripted model that can see describes the
GIF, and the stand-in child of the moderate-scan tests judges the words.
"""

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import azathoth.cli.bootstrap as cli_bootstrap
import pytest
from azathoth.cli import DATABASE_ENVIRONMENT_VARIABLE as AZATHOTH_DATABASE
from azathoth.providers import DeterministicLanguageModel, LanguageModelRegistry, ModelCatalog
from azathoth.tools import encode_tool_document

import tests.fixtures.stand_in_judge_tool as stand_in_judge_tool
from elder_sign.build import DOCUMENTS, build_document
from elder_sign.canary import FROZEN, FROZEN_FLAG
from elder_sign.judging import (
    JUDGE_MEDIA_ID,
    MODERATE_SCAN_ID,
    RECORD_MEDIA_ID,
    RESOLVE_COMMENTS_ID,
    AzathothRunner,
    Judgment,
    WorkflowError,
    held_back,
    judge,
)
from elder_sign.platforms import Comment, FakePlatform, Scan
from elder_sign.shared.store import (
    DATABASE_ENVIRONMENT_VARIABLE,
    connect,
    read_description,
    read_standing,
    write_flag,
    write_standing,
)
from tests.test_media_judging import JUDGMENT, VISION, cli, png
from tests.test_moderate_scan import CLASSIFY_COMMENT_ID, WORKFLOWS, stand_in_child

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"

GIF_KEY = "m:giphy:3o7TKSjRrfIPjeiVyM"

GONE = "https://media.giphy.com/media/GoneGoneGone/giphy.gif"

GONE_KEY = "m:giphy:GoneGoneGone"

REEL = "2027199361330354"


def scan_of(
    comments: list[Comment],
) -> Scan:
    """Return a scan of the reel with these comments."""

    return {"platform": "facebook", "post_id": REEL, "comments": comments}


def comment(
    comment_id: str,
    author: str,
    text: str | None,
    *,
    parent: str | None = None,
    media: str | None = None,
) -> Comment:
    """Return one comment, with a GIF when given one."""

    found: dict[str, Any] = {
        "id": comment_id,
        "parent_id": parent,
        "author_id": author,
        "text": text,
    }

    if media is not None:
        found["media"] = [{"kind": "gif", "url": media, "provider": "giphy"}]

    return cast(Comment, found)


class ScriptedRunner:
    """Answers each workflow as told, and records what it was asked."""

    def __init__(
        self,
        undescribed: list[dict[str, Any]] | None = None,
        *,
        judge_fails: bool = False,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._undescribed = undescribed or []
        self._judge_fails = judge_fails

    def run(
        self,
        workflow_id: str,
        payload: Any = None,
        *,
        input_file: Path | None = None,
    ) -> Any:
        if workflow_id == JUDGE_MEDIA_ID:
            assert input_file is not None
            self.calls.append((workflow_id, input_file.read_bytes()))

            if self._judge_fails:
                raise WorkflowError("Azathoth could not run judge-media: no model can see")

            return {"description": "A clown.", "hostile": True}

        self.calls.append((workflow_id, payload))

        if workflow_id == RESOLVE_COMMENTS_ID:
            return {"undescribed_media": self._undescribed}

        if workflow_id == RECORD_MEDIA_ID:
            return {"media_key": payload["media_key"], "described": True, "hostile": True}

        ids = [entry["id"] for entry in payload["comments"]]

        return {
            "verdicts": [
                {"comment_id": cid, "verdict": "hostile", "layer": "model", "source": "model"}
                for cid in ids
            ],
            "layers": {"model": len(ids)},
            "model": {"groups": len(ids), "unreadable": 0},
        }

    def asked(self) -> list[str]:
        return [workflow_id for workflow_id, _ in self.calls]


def platform_with_gif(
    account_id: str | None = None,
) -> FakePlatform:
    """Return a platform that serves the GIF's still and nothing for the gone one."""

    platform = FakePlatform(account_id=account_id)
    platform.add_media(GIF, png())

    return platform


@pytest.fixture
def state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Give judging a fresh state file, and the test a connection to it."""

    path = tmp_path / "elder-sign.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)

    try:
        yield connection
    finally:
        connection.close()


# The steps


def test_a_scan_with_nothing_new_to_describe_is_resolved_then_judged_whole(
    state: sqlite3.Connection,
) -> None:
    runner = ScriptedRunner()
    scan = scan_of([comment("c1", "a", "hi"), comment("c2", "b", "yo", parent="c1")])

    judgment = judge(scan, platform_with_gif(), runner)

    assert runner.asked() == [RESOLVE_COMMENTS_ID, MODERATE_SCAN_ID]
    assert runner.calls[1][1] == scan
    assert judgment.described == 0
    assert judgment.held == ()
    assert judgment.counts() == {"hostile": 2, "friendly": 0, "uncertain": 0}


def test_new_media_is_fetched_described_and_kept_before_the_words_are_judged(
    state: sqlite3.Connection,
) -> None:
    runner = ScriptedRunner([{"media_key": GIF_KEY, "kind": "gif", "url": GIF}])
    scan = scan_of([comment("c1", "troll", None, media=GIF)])

    judgment = judge(scan, platform_with_gif(), runner)

    assert runner.asked() == [
        RESOLVE_COMMENTS_ID,
        JUDGE_MEDIA_ID,
        RECORD_MEDIA_ID,
        MODERATE_SCAN_ID,
    ]
    assert runner.calls[1][1] == png()
    assert runner.calls[2][1] == {
        "media_key": GIF_KEY,
        "judgment": {"description": "A clown.", "hostile": True},
        "model": None,
    }
    assert judgment.described == 1
    assert judgment.held == ()


def test_comments_whose_media_or_parents_media_cannot_be_fetched_are_held(
    state: sqlite3.Connection,
) -> None:
    runner = ScriptedRunner([{"media_key": GONE_KEY, "kind": "gif", "url": GONE}])
    scan = scan_of(
        [
            comment("c1", "troll", None, media=GONE),
            comment("c2", "troll-2", "lol", parent="c1"),
            comment("c3", "friend", "hi"),
        ]
    )

    judgment = judge(scan, platform_with_gif(), runner)

    assert runner.asked() == [RESOLVE_COMMENTS_ID, MODERATE_SCAN_ID]
    assert [entry["id"] for entry in runner.calls[1][1]["comments"]] == ["c3"]
    assert judgment.held == ("c1", "c2")
    assert judgment.undescribed == (GONE_KEY,)


def test_media_a_model_cannot_describe_holds_its_comments_and_the_rest_are_judged(
    state: sqlite3.Connection,
) -> None:
    runner = ScriptedRunner([{"media_key": GIF_KEY, "kind": "gif", "url": GIF}], judge_fails=True)
    scan = scan_of([comment("c1", "troll", None, media=GIF), comment("c2", "friend", "hi")])

    judgment = judge(scan, platform_with_gif(), runner)

    assert judgment.held == ("c1",)
    assert judgment.undescribed == (GIF_KEY,)
    assert judgment.counts()["hostile"] == 1


def test_only_the_comments_with_undescribed_media_or_such_a_parent_are_held_back() -> None:
    scan = scan_of(
        [
            comment("c1", "a", "hi", media=GIF),
            comment("c2", "b", "yo", parent="c1"),
            comment("c3", "c", "sup", parent="c2"),
            comment("c4", "d", None, media=GONE),
        ]
    )

    assert held_back(scan, {GIF_KEY}) == ["c1", "c2"]
    assert held_back(scan, {GONE_KEY}) == ["c4"]
    assert held_back(scan, set()) == []


def test_a_failing_moderate_scan_is_raised(
    state: sqlite3.Connection,
) -> None:
    class Failing(ScriptedRunner):
        def run(self, workflow_id: str, payload: Any = None, **options: Any) -> Any:
            if workflow_id == MODERATE_SCAN_ID:
                raise WorkflowError("Azathoth could not run moderate-scan: boom")

            return super().run(workflow_id, payload, **options)

    with pytest.raises(WorkflowError, match="moderate-scan"):
        judge(scan_of([comment("c1", "a", "hi")]), platform_with_gif(), Failing())


# The person's own account


def test_the_signed_in_account_is_trusted_before_anything_is_judged(
    state: sqlite3.Connection,
) -> None:
    runner = ScriptedRunner()

    judge(scan_of([comment("c1", "me", "my answer")]), platform_with_gif("me"), runner)

    assert read_standing(state, "facebook", "me") == "trusted"


def test_the_signed_in_account_is_trusted_even_if_it_was_once_blocked(
    state: sqlite3.Connection,
) -> None:
    write_standing(state, "facebook", "me", "blocked")

    judge(scan_of([comment("c1", "me", "hi")]), platform_with_gif("me"), ScriptedRunner())

    assert read_standing(state, "facebook", "me") == "trusted"


def test_a_platform_signed_in_as_no_one_trusts_no_one(
    state: sqlite3.Connection,
) -> None:
    judge(scan_of([comment("c1", "a", "hi")]), platform_with_gif(), ScriptedRunner())

    assert state.execute("SELECT COUNT(*) FROM authors").fetchone() == (0,)


# Through Azathoth itself


@pytest.fixture
def installed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[sqlite3.Connection]:
    """Install Elder Sign with a scripted model that can see, and the stand-in child promoted."""

    monkeypatch.setenv(AZATHOTH_DATABASE, str(tmp_path / "azathoth.db"))
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(tmp_path / "elder-sign.db"))
    monkeypatch.delenv("AZATHOTH_CONTENT_STORE", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setattr(
        cli_bootstrap, "_load_current_models", lambda _configuration: ModelCatalog(models=(VISION,))
    )
    monkeypatch.setattr(
        cli_bootstrap,
        "_load_language_models",
        lambda *, configuration, models: LanguageModelRegistry(
            {
                VISION.identifier: DeterministicLanguageModel(
                    provider=VISION.provider,
                    model=VISION.model,
                    response_text=json.dumps(JUDGMENT),
                )
            }
        ),
    )

    stand_in_tool = tmp_path / "stand-in-judge.json"
    stand_in_tool.write_text(encode_tool_document(build_document(stand_in_judge_tool)))
    child = tmp_path / "stand-in-classify-comment.json"
    child.write_text(json.dumps(stand_in_child()))

    cli(capsys, "init")
    cli(capsys, "model", "authorize", VISION.identifier)

    for path in (*sorted(DOCUMENTS.glob("*.json")), stand_in_tool):
        cli(capsys, "tool", "import", str(path))

    for path in sorted(WORKFLOWS.glob("*.json")):
        if path.name != "classify-comment.json":
            cli(capsys, "workflow", "import", str(path))

    cli(capsys, "workflow", "import", str(child))
    cli(capsys, "workflow", "promote", CLASSIFY_COMMENT_ID)

    connection = connect(tmp_path / "elder-sign.db")

    try:
        yield connection
    finally:
        connection.close()


SCAN = [
    comment("c1", "friend", "slay queen 👑"),
    comment("c2", "troll-1", "you will never be a woman"),
    comment("c3", "troll-2", None, parent="c1", media=GIF),
    comment("c4", "troll-3", None, media=GONE),
]


def verdicts(
    judgment: Judgment,
) -> dict[str, tuple[str, str]]:
    """Return each judged comment's verdict and the layer that decided it."""

    return {
        entry["comment_id"]: (entry["verdict"], entry["layer"])
        for entry in judgment.outcome["verdicts"]
    }


def test_the_persons_own_comments_never_reach_a_model(
    installed: sqlite3.Connection,
) -> None:
    mine = [
        comment("c1", "troll-1", "you will never be a woman"),
        comment("c2", "me", "I am a woman, and this is my reel", parent="c1"),
    ]

    judgment = judge(scan_of(mine), platform_with_gif("me"), AzathothRunner())

    assert verdicts(judgment) == {
        "c1": ("hostile", "model"),
        "c2": ("friendly", "trusted_author"),
    }
    assert judgment.outcome["model"]["groups"] == 1


def test_a_scan_is_judged_through_azathoth_its_gif_described_once(
    installed: sqlite3.Connection,
) -> None:
    platform = platform_with_gif()

    first = judge(scan_of(SCAN), platform, AzathothRunner())

    assert verdicts(first) == {
        "c1": ("friendly", "model"),
        "c2": ("hostile", "model"),
        "c3": ("hostile", "media"),
    }
    assert first.described == 1
    assert first.held == ("c4",)
    assert first.undescribed == (GONE_KEY,)
    assert read_description(installed, GIF_KEY) == JUDGMENT["description"]

    second = judge(scan_of(SCAN), platform, AzathothRunner())

    assert second.described == 0
    assert second.outcome["model"] == {"groups": 0, "unreadable": 0, "escalated": 0}
    assert verdicts(second)["c3"] == ("hostile", "media")
    assert second.held == ("c4",)
    # the gone GIF is tried again each run; the described one never is
    assert platform.calls["fetch_media"] == 3


def test_azathoth_runs_even_where_an_event_loop_is_running_as_the_browser_keeps_one(
    installed: sqlite3.Connection,
) -> None:
    async def inside_a_running_loop() -> Any:
        return AzathothRunner().run(RESOLVE_COMMENTS_ID, scan_of([comment("c1", "a", "hi")]))

    resolution = asyncio.run(inside_a_running_loop())

    assert resolution["needs_inference"][0]["comment_ids"] == ["c1"]


def test_a_workflow_azathoth_does_not_have_is_an_error_saying_so(
    installed: sqlite3.Connection,
) -> None:
    with pytest.raises(WorkflowError, match="Azathoth could not run"):
        AzathothRunner().run("e1de7516-0000-4000-8000-000000000999", {})


# The command


def test_the_judge_command_judges_and_keeps_the_verdicts_privately(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import elder_sign.cli as command
    import elder_sign.platforms.facebook.reader as reader

    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(tmp_path / "state.db"))
    platform = platform_with_gif()
    platform.add_post(REEL, [comment("c1", "troll", None, media=GONE), comment("c2", "a", "hi")])

    @contextmanager
    def facebook(settings: Any = None, pacer: Any = None, **options: Any) -> Iterator[Any]:
        yield platform

    monkeypatch.setattr(reader, "facebook", facebook)
    runner = ScriptedRunner([{"media_key": GONE_KEY, "kind": "gif", "url": GONE}])
    out = tmp_path / "verdicts.json"
    options = command.build_parser().parse_args(
        ["judge", "facebook", f"https://www.facebook.com/reel/{REEL}", "--out", str(out)]
    )

    assert command._judge(options, runner=runner) == 0

    printed = capsys.readouterr().out
    report = json.loads(out.read_text())

    assert "Judged 1 comments on reel 2027199361330354: 1 hostile, 0 friendly" in printed
    assert "Decided 1 by model." in printed
    assert "(0 unreadable, 0 judged again by Opus 5.5)" in printed
    assert "Held 1 comment for the next run" in printed
    assert "Nothing on Facebook was changed." in printed
    assert "it would delete 0, hide 0 and unhide 0" in printed
    assert "0 uncertain left for you, 1 too new to act on" in printed
    assert report["plan"]["waiting"] == ["c2"]
    assert report["held"] == ["c1"]
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_the_forget_command_forgets_a_models_verdicts_and_keeps_yours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from elder_sign.cli import main
    from elder_sign.shared.store import Verdict, read_verdict, write_verdict

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)
    model = Verdict(verdict="hostile", source="model", model="m", reason=None)
    human = Verdict(verdict="friendly", source="human", model=None, reason=None)
    write_verdict(connection, "transphobic-harassment", "content", "c:1", model)
    write_verdict(connection, "transphobic-harassment", "content", "c:2", human)

    assert main(["verdicts", "forget"]) == 1
    assert main(["verdicts", "forget", "--model"]) == 0
    assert "Forgot 1 verdict a model gave" in capsys.readouterr().out
    assert read_verdict(connection, "transphobic-harassment", "content", "c:1") is None
    assert read_verdict(connection, "transphobic-harassment", "content", "c:2") == human
    connection.close()


def test_the_judge_command_judges_nothing_while_frozen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import elder_sign.cli as command

    path = tmp_path / "state.db"
    monkeypatch.setenv(DATABASE_ENVIRONMENT_VARIABLE, str(path))
    connection = connect(path)
    write_flag(connection, FROZEN_FLAG.format(platform="facebook"), FROZEN, reason="lost replies")
    connection.close()
    runner = ScriptedRunner()
    options = command.build_parser().parse_args(
        ["judge", "facebook", f"https://www.facebook.com/reel/{REEL}"]
    )

    assert command._judge(options, runner=runner) == 3
    assert "frozen on facebook" in capsys.readouterr().err
    assert runner.calls == []
