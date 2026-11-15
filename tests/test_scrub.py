"""Tests proving a scrubbed capture keeps every shape and nothing that identifies anyone."""

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from elder_sign.cli import main
from elder_sign.scrub import (
    LeakError,
    Scrubber,
    json_values,
    leaks,
    mask,
    page_payloads,
    prune,
    scrub,
)

KEY = b"k" * 32


def scrubbed(
    value: Any,
    scrubber: Scrubber | None = None,
) -> Any:
    """Return one JSON value scrubbed with a fixed key."""

    return (scrubber or Scrubber(KEY)).value(value)


# Words


def test_words_are_masked_their_length_case_and_spacing_kept() -> None:
    assert mask("Eris Discordia, 55! 🏳️‍⚧️") == "Xxxx Xxxxxxxxx, 00! 🏳️‍⚧️"


@pytest.mark.parametrize("key", ["text", "name", "author_name", "short_name", "body_text"])
def test_strings_under_keys_that_hold_words_are_masked(
    key: str,
) -> None:
    assert scrubbed({key: "Kim"}) == {key: "Xxx"}


def test_strings_with_spaces_are_masked_wherever_they_are() -> None:
    assert scrubbed({"whatever": "go away sir"}) == {"whatever": "xx xxxx xxx"}


def test_a_mention_still_lands_on_the_masked_name() -> None:
    comment = {
        "text": "Eris you are a queen",
        "ranges": [{"offset": 0, "length": 4, "entity": {"name": "Eris"}}],
    }

    result = scrubbed(comment)
    (mention,) = result["ranges"]

    assert (
        result["text"][mention["offset"] : mention["offset"] + mention["length"]]
        == (mention["entity"]["name"])
    )


# IDs and tokens


def test_an_id_becomes_the_same_replacement_everywhere_with_its_shape_kept() -> None:
    result = scrubbed({"id": "1000123456789", "parent": {"id": "1000123456789"}, "other": "77"})

    assert result["id"] == result["parent"]["id"]
    assert result["id"] != "1000123456789"
    assert len(result["id"]) == 13 and result["id"].isdigit() and result["id"][0] != "0"
    assert result["other"] != result["id"]


def test_a_token_keeps_its_letters_and_digits_in_their_places() -> None:
    replacement = Scrubber(KEY).token("Ab12cD")

    assert [character.isdigit() for character in replacement] == [
        False,
        False,
        True,
        True,
        False,
        False,
    ]
    assert replacement[0].isupper() and replacement[1].islower() and replacement[5].isupper()


def test_each_scrub_draws_its_own_key_so_no_replacement_can_be_traced() -> None:
    assert Scrubber().token("1000123456789") != Scrubber().token("1000123456789")


def test_an_id_hidden_in_base64_matches_the_bare_id_once_scrubbed() -> None:
    hidden = base64.b64encode(b"feedback:1000123456789").decode()
    scrubber = Scrubber(KEY)

    result = scrubbed({"feedback_id": hidden, "post_id": "1000123456789"}, scrubber)
    decoded = base64.b64decode(result["feedback_id"]).decode()

    assert decoded == f"feedback:{result['post_id']}"


def test_structure_is_kept_as_it_is() -> None:
    value = {
        "__typename": "Comment",
        "ordering": "RANKED_THREADED",
        "count": 42,
        "created_time": 1763170000,
        "is_author": True,
        "missing": None,
    }

    assert scrubbed(value) == value


def test_keys_holding_ids_are_replaced_like_ids() -> None:
    result = scrubbed({"1000123456789": {"id": "1000123456789"}})
    ((key, inner),) = result.items()

    assert key == inner["id"] != "1000123456789"


# URLs


def test_a_url_keeps_its_host_extension_and_well_known_words() -> None:
    scrubber = Scrubber(KEY)
    url = "https://media2.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif?cid=abc123&rid=giphy.gif"

    result = scrubber.string(url, "url")

    assert result.startswith("https://media2.giphy.com/media/")
    assert result.split("/")[4] == scrubber.token("3o7TKSjRrfIPjeiVyM")
    assert result.split("?")[0].endswith(".gif")
    assert "abc123" not in result and "cid=" in result


def test_a_url_wrapped_in_facebooks_keeps_its_host_and_still_decodes() -> None:
    from urllib.parse import parse_qs, urlsplit

    scrubber = Scrubber(KEY)
    inner = "https://media1.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif?cid=abc123"
    wrapped = (
        "https://external.xx.fbcdn.net/emg1/v/t13/123456789?url="
        + inner.replace(":", "%3A").replace("/", "%2F").replace("?", "%3F").replace("=", "%3D")
        + "&_nc_sid=abc"
    )

    result = scrubber.string(wrapped, "uri")
    (decoded,) = parse_qs(urlsplit(result).query)["url"]

    assert decoded.startswith("https://media1.giphy.com/media/")
    assert decoded.split("/")[4] == scrubber.token("3o7TKSjRrfIPjeiVyM")
    assert "abc123" not in decoded


def test_a_profile_url_loses_the_name_in_it() -> None:
    result = Scrubber(KEY).string("https://www.facebook.com/eris.discordia", "url")

    assert result.startswith("https://www.facebook.com/")
    assert "eris" not in result and "discordia" not in result


# Bodies and pages


def test_a_graphql_body_is_every_json_value_it_holds_without_the_guard() -> None:
    body = 'for (;;);{"data": {"a": 1}}\n{"label": "x", "data": {"b": 2}}\n'

    assert json_values(body) == [{"data": {"a": 1}}, {"label": "x", "data": {"b": 2}}]


def test_a_page_is_only_the_json_in_its_scripts_that_mentions_comments() -> None:
    html = (
        "<html><body><p>Eris Discordia</p>"
        '<script type="application/json" data-sjs>{"require": [["Comment", {"text": "hi"}]]}'
        "</script>"
        '<script type="application/json">{"theme": "dark"}</script>'
        "<script>var x = 1;</script></body></html>"
    )

    assert page_payloads(html) == [{"require": [["Comment", {"text": "hi"}]]}]


# Records and leaks


def capture_records() -> list[dict[str, Any]]:
    """Return a small capture, as a capture writes one."""

    page = (
        '<script type="application/json">{"comment": {"__typename": "Comment", '
        '"id": "5551234567", "author": {"name": "Kim Troll", "id": "1000987654321"}, '
        '"body": {"text": "you will never be a woman"}}}</script>'
        '<script type="application/json">{"comment_composer": {"theme": "dark"}}</script>'
    )
    body = json.dumps(
        {
            "data": {
                "node": {
                    "__typename": "Comment",
                    "id": "5559876543",
                    "parent": {"id": "5551234567"},
                    "author": {"name": "Ally Friend", "id": "1000111222333"},
                    "body": {"text": "slay queen"},
                }
            }
        }
    )

    return [
        {
            "kind": "graphql",
            "url": "https://www.facebook.com/api/graphql/",
            "status": 200,
            "friendly_name": "OhaiWebPublicKeyConfigQuery",
            "doc_id": "13579",
            "variables": {},
            "body": json.dumps({"data": {"key": "abc"}}),
        },
        {
            "kind": "document",
            "url": "https://www.facebook.com/reel/1234567890123",
            "status": 200,
            "body": page,
        },
        {
            "kind": "graphql",
            "url": "https://www.facebook.com/api/graphql/",
            "status": 200,
            "friendly_name": "CommentListComponentsRootQuery",
            "doc_id": "24680",
            "variables": {"feedbackID": "1234567890123", "first": 10},
            "body": "for (;;);" + body,
        },
    ]


def test_a_scrubbed_capture_keeps_who_replied_to_what_and_nothing_else() -> None:
    lines = list(scrub(capture_records(), Scrubber(KEY)))
    document, graphql = (json.loads(line) for line in lines)
    (page,) = document["payloads"]
    (node,) = [payload["data"]["node"] for payload in graphql["payloads"]]
    text = "".join(lines)

    assert node["parent"]["id"] == page["comment"]["id"]
    assert node["__typename"] == "Comment"
    assert graphql["friendly_name"] == "CommentListComponentsRootQuery"
    assert graphql["doc_id"] == "24680"
    assert graphql["variables"]["first"] == 10
    assert document["url"].endswith(graphql["variables"]["feedbackID"])

    for secret in (
        "Kim Troll",
        "Ally Friend",
        "you will never be a woman",
        "slay queen",
        "1000987654321",
        "1000111222333",
        "5551234567",
        "1234567890123",
    ):
        assert secret not in text


def test_a_scrub_that_leaks_writes_nothing() -> None:
    scrubber = Scrubber(KEY)
    scrubber.masked.add("Kim Troll")

    assert leaks(scrubber, "... Kim Troll ...") == ["Kim Troll"]

    class Leaky(Scrubber):
        def record(
            self, record: dict[str, Any], payloads: list[Any] | None = None
        ) -> dict[str, Any]:
            self.masked.add("Kim Troll")
            return {"url": "", "variables": None, "payloads": [{"name": "Kim Troll"}]}

    with pytest.raises(LeakError, match="nothing was written"):
        list(scrub(capture_records(), Leaky(KEY)))


def test_only_what_is_about_comments_is_kept_unless_everything_is_asked_for() -> None:
    kept = [json.loads(line) for line in scrub(capture_records(), Scrubber(KEY))]
    everything = [
        json.loads(line) for line in scrub(capture_records(), Scrubber(KEY), everything=True)
    ]

    assert [record["friendly_name"] for record in kept] == [None, "CommentListComponentsRootQuery"]
    assert len(kept[0]["payloads"]) == 1
    assert [record["friendly_name"] for record in everything][0] == "OhaiWebPublicKeyConfigQuery"
    assert len(everything[1]["payloads"]) == 2


def test_a_posts_comment_count_is_about_comments() -> None:
    record = {
        "kind": "graphql",
        "url": "https://www.facebook.com/api/graphql/",
        "body": json.dumps({"data": {"feedback": {"total_comment_count": 5}}}),
    }

    (line,) = scrub([record], Scrubber(KEY))

    assert json.loads(line)["payloads"] == [{"data": {"feedback": {"total_comment_count": 5}}}]


def test_a_payload_is_pruned_to_what_leads_to_a_comment_or_a_count() -> None:
    comment = {"__typename": "Comment", "id": "1", "feedback": {"reactors": [{"id": "2"}]}}
    payload = {
        "data": {
            "viewer": {"settings": {"autoplay": True}},
            "story": {
                "id": "s",
                "feedback": {"total_comment_count": 5, "reactors": {"edges": [{"id": "9"}]}},
                "comments": {"total_count": 1, "edges": [{"cursor": "c", "node": comment}, {}]},
            },
            "next_reels": [{"id": "r", "player": {"src": "x"}}],
        },
        "extensions": {"is_final": True},
    }

    assert prune(payload) == {
        "data": {
            "story": {
                "id": "s",
                "feedback": {"total_comment_count": 5},
                "comments": {"total_count": 1, "edges": [{"cursor": "c", "node": comment}]},
            }
        }
    }


def test_a_payload_leading_nowhere_is_pruned_away() -> None:
    assert prune({"data": {"viewer": {"id": "1"}}}) is None
    assert prune([{"a": 1}, []]) is None
    assert prune("text") is None


# Addresses and numbers


@pytest.mark.parametrize("key", ["uri_token", "vanity", "profile_url", "handle", "slug"])
def test_every_word_of_an_address_is_replaced_because_it_is_made_of_a_name(
    key: str,
) -> None:
    result = Scrubber(KEY).string("Eris.Discordia.M", key)

    assert "Eris" not in result and "Discordia" not in result
    assert result.count(".") == 2


def test_a_profile_link_with_a_dotted_name_loses_both_names() -> None:
    result = Scrubber(KEY).string("https://www.facebook.com/Kim.Troll", "link_url")

    assert result.startswith("https://www.facebook.com/")
    assert "Kim" not in result and "Troll" not in result


def test_a_number_under_an_ids_key_is_replaced_like_an_id() -> None:
    scrubber = Scrubber(KEY)
    result = scrubbed(
        {"userId": 782080741, "actor_id": 1000987654321, "count": 782080741, "id": 7},
        scrubber,
    )

    assert result["userId"] == int(scrubber.token("782080741")) != 782080741
    assert result["actor_id"] != 1000987654321
    assert result["count"] == 782080741
    assert result["id"] == 7


# What is searched for


def test_a_word_kept_as_a_key_or_a_code_name_has_leaked_nothing() -> None:
    scrubber = Scrubber(KEY)
    records = [
        {
            "url": "",
            "variables": None,
            "payloads": [
                {
                    "Kim Troll": 1,
                    "preloader": "adp_CometLogoutHandlerQueryRelayPreloader_5re32",
                }
            ],
        }
    ]
    scrubber.masked.update({"Kim Troll", "CometLogoutHandlerQuery"})

    assert leaks(scrubber, records) == []


def test_an_id_is_searched_for_as_a_whole_token() -> None:
    scrubber = Scrubber(KEY)
    scrubber.replaced.add("318394")
    records = [{"url": "", "variables": None, "payloads": ["31839469152681680"]}]

    assert leaks(scrubber, records) == []

    records[0]["payloads"] = ["x_318394_y"]

    assert leaks(scrubber, records) == ["318394"]


# The command


def test_the_scrub_command_writes_beside_the_capture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "facebook-1.jsonl"
    source.write_text("".join(json.dumps(record) + "\n" for record in capture_records()))

    assert main(["scrub", str(source)]) == 0

    output = tmp_path / "facebook-1.scrubbed.jsonl"

    assert "Scrubbed 2 responses" in capsys.readouterr().out
    assert len(output.read_text().splitlines()) == 2
    assert "Kim Troll" not in output.read_text()
