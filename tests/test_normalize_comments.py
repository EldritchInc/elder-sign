"""Tests proving normalize_comments keys a whole scan, through Azathoth's process runtime."""

import asyncio
from typing import Any, cast

import pytest
from azathoth.tools import ProcessToolExecutor, decode_tool_document

from elder_sign.build import DOCUMENTS
from elder_sign.shared.keys import media_key

DOCUMENT = DOCUMENTS / "normalize-comments.json"

GIF = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"


def normalize(
    comments: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Run the built tool on one scan, returning its comments by ID."""

    (implementation,) = decode_tool_document(DOCUMENT.read_text(encoding="utf-8")).implementations
    scan: dict[str, Any] = {"platform": "facebook", "post_id": "reel-1", "comments": comments}

    output = asyncio.run(ProcessToolExecutor().execute(implementation, {"scan": scan}))
    keyed = cast(dict[str, Any], output["scan"])

    assert keyed["platform"] == "facebook"
    assert keyed["post_id"] == "reel-1"

    return {comment["id"]: comment for comment in keyed["comments"]}


def comment(
    comment_id: str,
    text: str | None = None,
    *,
    parent: str | None = None,
    author: str = "troll",
    **fields: Any,
) -> dict[str, Any]:
    """Return one comment as an adapter reads it."""

    return {"id": comment_id, "parent_id": parent, "author_id": author, "text": text, **fields}


def test_a_swarms_copy_pastes_share_their_content_key() -> None:
    keyed = normalize(
        [
            comment("c1", "TRANNY"),
            comment("c2", "tr4nnyyyy!!!", author="troll-2"),
            comment("c3", "t r a n n y", author="troll-3"),
            comment("c4", "love this"),
        ]
    )

    assert keyed["c1"]["keys"]["content"] == keyed["c2"]["keys"]["content"]
    assert keyed["c2"]["keys"]["content"] == keyed["c3"]["keys"]["content"]
    assert keyed["c1"]["keys"]["content"] != keyed["c4"]["keys"]["content"]
    assert keyed["c3"]["normalized"] == "tranny"


def keyed_scan(
    scan: dict[str, Any],
) -> dict[str, Any]:
    """Run the built tool on one whole scan, returning what it returns."""

    (implementation,) = decode_tool_document(DOCUMENT.read_text(encoding="utf-8")).implementations
    output = asyncio.run(ProcessToolExecutor().execute(implementation, {"scan": scan}))

    return cast(dict[str, Any], output["scan"])


def test_the_posts_caption_and_owner_are_passed_on() -> None:
    scan = {
        "platform": "facebook",
        "post_id": "reel-1",
        "caption": "Lesbian first date today 💜",
        "owner_id": "me",
        "comments": [comment("c1", "have fun")],
    }

    keyed = keyed_scan(scan)

    assert keyed["caption"] == "Lesbian first date today 💜"
    assert keyed["owner_id"] == "me"


def test_a_top_level_comment_has_one_context_per_post() -> None:
    def context(post_id: str) -> str:
        scan = {"platform": "facebook", "post_id": post_id, "comments": [comment("c1", "oh dear")]}

        return str(keyed_scan(scan)["comments"][0]["keys"]["context"])

    assert context("reel-1") != context("reel-2")
    assert context("reel-1") == context("reel-1")


def test_a_comment_keeps_every_field_the_adapter_gave_it() -> None:
    keyed = normalize([comment("c1", "hi", author_name="Kim", created_at="2026-11-14T22:00:00Z")])

    assert keyed["c1"]["author_name"] == "Kim"
    assert keyed["c1"]["created_at"] == "2026-11-14T22:00:00Z"
    assert keyed["c1"]["text"] == "hi"


def test_one_reply_to_different_parents_has_one_content_and_two_contexts() -> None:
    keyed = normalize(
        [
            comment("hate", "trans women are men"),
            comment("love", "trans women are women", author="friend"),
            comment("r1", "lol exactly", parent="hate"),
            comment("r2", "lol exactly", parent="love"),
        ]
    )

    assert keyed["r1"]["keys"]["content"] == keyed["r2"]["keys"]["content"]
    assert keyed["r1"]["keys"]["context"] != keyed["r2"]["keys"]["context"]


def test_a_reply_listed_before_its_parent_still_has_its_context() -> None:
    keyed = normalize([comment("r1", "lol exactly", parent="p1"), comment("p1", "kind words")])

    assert keyed["r1"]["keys"]["context"] is not None


def test_a_reply_to_a_parent_outside_the_scan_has_no_context() -> None:
    keyed = normalize([comment("r1", "lol exactly", parent="gone")])

    assert keyed["r1"]["keys"]["content"] is not None
    assert keyed["r1"]["keys"]["context"] is None


def test_a_gif_reply_is_keyed_by_the_gif_wherever_it_is_hosted() -> None:
    keyed = normalize(
        [
            comment("g1", None, media=[{"kind": "gif", "url": GIF}]),
            comment("g2", "", media=[{"kind": "gif", "url": GIF + "?cid=xyz"}]),
            comment("g3", "lol", media=[{"kind": "gif", "url": GIF}]),
        ]
    )

    assert keyed["g1"]["keys"]["text"] is None
    assert keyed["g1"]["keys"]["media"] == [media_key({"url": GIF})]
    assert keyed["g1"]["keys"]["content"] == keyed["g2"]["keys"]["content"]
    assert keyed["g1"]["keys"]["content"] != keyed["g3"]["keys"]["content"]


def test_a_comment_with_neither_words_nor_media_has_no_keys() -> None:
    keyed = normalize([comment("e1", "...", media=[{"kind": "sticker"}])])

    assert keyed["e1"]["keys"] == {"text": None, "media": [], "content": None, "context": None}


def test_mentions_do_not_split_a_swarm() -> None:
    keyed = normalize(
        [
            comment("m1", "Eris go away", mentions=["Eris"]),
            comment("m2", "Kim go away", mentions=["Kim"]),
        ]
    )

    assert keyed["m1"]["keys"]["content"] == keyed["m2"]["keys"]["content"]


def test_two_comments_with_one_id_fail_the_tool() -> None:
    with pytest.raises(Exception, match="exited|share the ID"):
        normalize([comment("c1", "a"), comment("c1", "b")])
