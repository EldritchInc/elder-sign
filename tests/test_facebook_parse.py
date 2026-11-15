"""Tests proving comments and counts are read out of what Facebook really sends.

The canary fixture is a real capture of a reel left with two top-level
comments, a reply, a GIF reply and a comment that mentions someone, scrubbed:
every word masked and every ID replaced, every shape kept.
"""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from elder_sign.platforms import fingerprint_of, validate_scan
from elder_sign.platforms.facebook.parse import (
    PostDetails,
    comment_counts,
    comments_in,
    created_at,
    media_of,
    mentions_of,
    post_id_of,
    posts_in,
    unwrapped,
)
from elder_sign.shared.keys import media_key
from tests.tools import run_tool

CANARY = Path(__file__).parent / "fixtures" / "facebook" / "canary.jsonl"


def canary() -> list[dict[str, Any]]:
    """Return the canary capture's records."""

    return [json.loads(line) for line in CANARY.read_text(encoding="utf-8").splitlines()]


# The canary


def test_the_canary_holds_every_comment_once() -> None:
    comments = comments_in(canary())

    assert len(comments) == 5
    assert len({comment["id"] for comment in comments}) == 5


def test_the_canary_has_four_top_level_comments_and_a_reply_to_the_second() -> None:
    comments = comments_in(canary())
    top_level = [comment for comment in comments if comment["parent_id"] is None]
    (reply,) = [comment for comment in comments if comment["parent_id"] is not None]

    assert len(top_level) == 4
    assert reply["parent_id"] == top_level[1]["id"]


def test_every_comment_has_its_author_and_time() -> None:
    for comment in comments_in(canary()):
        assert comment["author_id"].isdigit()
        assert comment["author_name"]
        assert str(comment["created_at"]).endswith("Z")


def test_the_gif_reply_is_a_giphy_gif_with_no_words() -> None:
    (gif,) = [comment for comment in comments_in(canary()) if comment.get("media")]
    (item,) = gif["media"]

    assert gif["text"] is None
    assert item["kind"] == "gif"
    assert item.get("provider") == "giphy"
    assert str(item["url"]).startswith("https://media1.giphy.com/media/")
    assert str(media_key(item)).startswith("m:giphy:")


def test_the_mention_is_the_words_its_range_covers() -> None:
    (mentioning,) = [comment for comment in comments_in(canary()) if comment.get("mentions")]
    (mention,) = mentioning["mentions"]

    assert len(mention) == 17
    assert str(mentioning["text"]).startswith(mention)


def test_the_reels_page_counts_its_comments_replies_included() -> None:
    comments = comments_in(canary())
    (post_id,) = {post_id_of(comment["id"]) for comment in comments}

    assert post_id is not None
    assert comment_counts(canary())[post_id] == len(comments) == 5


def test_the_canary_reads_as_a_scan_the_workflows_take() -> None:
    comments = comments_in(canary())
    scan = validate_scan({"platform": "facebook", "post_id": "canary", "comments": comments})

    keyed = run_tool("normalize_comments", {"scan": scan})["scan"]["comments"]
    (gif,) = [comment for comment in keyed if comment.get("media")]

    assert fingerprint_of(scan).comment_count == 5
    assert fingerprint_of(scan).newest_comment_id == comments[3]["id"]
    assert gif["keys"]["media"][0].startswith("m:giphy:")
    assert all(comment["keys"]["content"] for comment in keyed)


# Shapes, one at a time


def node(
    **fields: Any,
) -> dict[str, Any]:
    """Return a comment node as Facebook sends one."""

    return {
        "__typename": "Comment",
        "id": "c1",
        "author": {"__typename": "User", "id": "100", "name": "Kim"},
        "created_time": 1794716932,
        "body": {"text": "hi", "ranges": []},
        **fields,
    }


def records(
    *values: Any,
) -> list[dict[str, Any]]:
    """Return scrubbed records holding some payloads."""

    return [{"kind": "graphql", "payloads": list(values)}]


def test_a_comment_is_found_however_deep_it_sits() -> None:
    payload = {"data": {"a": [{"b": {"edges": [{"node": node()}]}}]}}

    assert [comment["id"] for comment in comments_in(records(payload))] == ["c1"]


@pytest.mark.parametrize(
    "fields",
    [{"author": None}, {"author": {"id": 7}}, {"created_time": "soon"}, {"id": None}],
    ids=["no author", "numeric author", "text time", "no id"],
)
def test_an_object_that_is_not_a_readable_comment_is_passed_over(
    fields: dict[str, Any],
) -> None:
    assert comments_in(records(node(**fields))) == []


def test_a_comment_sent_twice_is_read_from_its_fuller_copy() -> None:
    thin = node()
    full = node(attachments=[], depth=1, legacy_fbid="5")

    assert len(comments_in(records(thin, full))) == 1
    assert len(comments_in(records(full, thin))) == 1


def test_a_comment_without_words_has_no_text_or_mentions() -> None:
    (comment,) = comments_in(records(node(body=None)))

    assert (comment["text"], comment["mentions"]) == (None, [])


def test_a_reply_names_its_direct_parent() -> None:
    (comment,) = comments_in(records(node(comment_direct_parent={"id": "p1"})))

    assert comment["parent_id"] == "p1"


def test_a_raw_capture_is_read_as_well_as_a_scrubbed_one() -> None:
    raw = [{"kind": "graphql", "body": "for (;;);" + json.dumps({"data": {"node": node()}})}]

    assert [comment["id"] for comment in comments_in(raw)] == ["c1"]


def test_times_are_utc() -> None:
    assert created_at(0) == "1970-01-01T00:00:00Z"


def test_mentions_count_utf16_so_an_emoji_before_one_counts_twice() -> None:
    text = "🏳️‍⚧️ Kim Troll you are a queen"
    offset = len("🏳️‍⚧️ ".encode("utf-16-le")) // 2
    ranges = [
        {"entity": {"__typename": "User"}, "offset": offset, "length": 9},
        {"entity": {"__typename": "Hashtag"}, "offset": 0, "length": 3},
        {"entity": {"__typename": "Page"}, "offset": "x", "length": 3},
    ]

    assert mentions_of(text, ranges) == ["Kim Troll"]


def test_a_url_facebook_wraps_is_unwrapped() -> None:
    inner = "https://media.giphy.com/media/abc/giphy.gif"
    wrapped = "https://external.xx.fbcdn.net/emg1/v/t13/1?url=" + inner.replace(":", "%3A").replace(
        "/", "%2F"
    )

    assert unwrapped(wrapped) == inner
    assert unwrapped("https://scontent.xx.fbcdn.net/v/t39/1_n.jpg?x=1") == (
        "https://scontent.xx.fbcdn.net/v/t39/1_n.jpg?x=1"
    )


def test_a_sticker_is_known_by_facebooks_own_id() -> None:
    sticker = media_of(
        {
            "style_list": ["sticker", "fallback"],
            "style_type_renderer": {
                "attachment": {
                    "media": {
                        "__typename": "Sticker",
                        "id": "369239263222822",
                        "image": {"uri": "https://scontent.xx.fbcdn.net/v/t39/s.png"},
                    }
                }
            },
        }
    )

    assert sticker == {
        "kind": "sticker",
        "url": "https://scontent.xx.fbcdn.net/v/t39/s.png",
        "provider": "facebook-sticker",
        "provider_id": "369239263222822",
    }


def test_a_photo_is_an_image() -> None:
    photo = media_of(
        {
            "style_list": ["photo", "fallback"],
            "media": {
                "__typename": "Photo",
                "image": {"uri": "https://scontent.xx.fbcdn.net/p.jpg"},
            },
        }
    )

    assert photo == {"kind": "image", "url": "https://scontent.xx.fbcdn.net/p.jpg"}


@pytest.mark.parametrize(
    "attachment",
    [None, "x", {}, {"style_list": ["share"], "media": {"__typename": "ExternalUrl"}}],
    ids=["none", "text", "empty", "a link"],
)
def test_an_attachment_with_no_media_posts_none(
    attachment: Any,
) -> None:
    assert media_of(attachment) is None


def encode(
    text: str,
) -> str:
    """Return text in base64, as Facebook hides an ID."""

    return base64.b64encode(text.encode()).decode()


def test_a_post_is_named_by_its_comments_ids() -> None:
    assert post_id_of(encode("comment:51414897876204490_7258463746082190")) == "51414897876204490"
    assert post_id_of(encode("feedback:51414897876204490")) is None
    assert post_id_of("not base64!") is None


def test_counts_are_read_from_any_post_that_holds_one() -> None:
    payload = {
        "data": {
            "post_id": "1",
            "feedback": {"total_comment_count": 3},
            "next": [{"post_id": "2", "feedback": {"total_comment_count": 0}}],
            "other": {"post_id": "3", "feedback": {"total_comment_count": None}},
        }
    }

    assert comment_counts(records(payload)) == {"1": 3, "2": 0}


def test_a_posts_pieces_are_put_together_by_its_post_id() -> None:
    payload = [
        {
            "post_id": "1",
            "url": "https://www.facebook.com/reel/77/",
            "feedback": {"total_comment_count": 4},
        },
        {"post_id": "1", "creation_time": 0},
        {"post_id": "2", "url": "https://www.facebook.com/watch/9"},
    ]

    posts = posts_in(records(payload))

    assert posts["1"] == PostDetails(
        post_id="1", reel_id="77", created=datetime(1970, 1, 1, tzinfo=UTC), comment_count=4
    )
    assert "2" not in posts


def test_the_canary_reels_post_is_known_by_its_reel() -> None:
    reel = canary()[0]["url"].rstrip("/").rsplit("/", 1)[1]
    (own,) = [details for details in posts_in(canary()).values() if details.reel_id == reel]

    assert own.comment_count == 5
    assert own.created == datetime(2026, 11, 15, 4, 26, 23, tzinfo=UTC)
    assert {post_id_of(comment["id"]) for comment in comments_in(canary())} == {own.post_id}
