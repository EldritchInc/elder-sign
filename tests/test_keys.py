"""Tests proving normalization folds a swarm's disguises together, and keeps meaning apart."""

import pytest

from elder_sign.shared.keys import (
    content_key,
    context_key,
    media_key,
    normalize_text,
    text_key,
)

# Disguises that fold together


@pytest.mark.parametrize(
    "disguised",
    [
        "tranny",
        "TRANNY",
        "tr4nny",
        "tr@nny",
        "trannyyyyy",
        "t r a n n y",
        "T.R.A.N.N.Y.",
        "t-r-a-n-n-y",
        "tr​anny",
        "tr⁠an﻿ny",
        "trаnny",  # Cyrillic а
        "trαnny",  # Greek α
        "𝐭𝐫𝐚𝐧𝐧𝐲",  # mathematical bold
        "ｔｒａｎｎｙ",  # fullwidth
        "trånny",
        "tranny!!!",
        "  tranny  ",
    ],
)
def test_every_disguise_of_a_word_folds_to_the_word(
    disguised: str,
) -> None:
    assert normalize_text(disguised) == "tranny"


def test_a_sentence_folds_word_by_word() -> None:
    assert normalize_text("U R A Tr4nny!!! 🤡🤡🤡") == "ura tranny 🤡"


def test_mentions_are_removed_so_one_reply_to_two_people_is_one_comment() -> None:
    first = normalize_text("Eris Discordia you're a man", ["Eris Discordia"])
    second = normalize_text("Kim you're a man", ["Kim"])

    assert first == second == "youre a man"


def test_a_longer_mention_is_removed_before_a_shorter_one_inside_it() -> None:
    assert normalize_text("Eris Discordia hi", ["Eris", "Eris Discordia"]) == "hi"


# Meaning kept apart


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("I have 3 cats", "i have 3 cats"),
        ("100 percent", "100 percent"),
        ("good god", "good god"),
        ("tr!p", "trip"),
        ("@$$hole", "asshole"),
        ("Café ❤️", "cafe ❤"),
        ("🏳️‍⚧️ forever", normalize_text("🏳⚧ forever")),
    ],
    ids=[
        "digits alone",
        "numbers",
        "double letters",
        "interior symbol",
        "leading leet",
        "accent and emoji",
        "zwj emoji",
    ],
)
def test_normal_text_keeps_its_meaning(
    text: str,
    normalized: str,
) -> None:
    assert normalize_text(text) == normalized


def test_emoji_are_kept_because_they_carry_meaning() -> None:
    assert normalize_text("🤡") != normalize_text("❤️")


def test_two_single_letters_are_not_joined() -> None:
    assert normalize_text("a b cool") == "a b cool"


# Text keys


def test_text_keys_are_equal_exactly_when_normalized_text_is() -> None:
    assert text_key(normalize_text("tr4nny")) == text_key(normalize_text("TRANNY"))
    assert text_key("tranny") != text_key("trannies")
    assert str(text_key("tranny")).startswith("t:")


def test_text_without_words_has_no_key() -> None:
    assert text_key(normalize_text("!!! ...")) is None
    assert text_key("") is None


# Media keys


@pytest.mark.parametrize(
    "url",
    [
        "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif",
        "https://media2.giphy.com/media/3o7TKSjRrfIPjeiVyM/200w.webp?cid=abc&rid=200w.webp",
        "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjEx/3o7TKSjRrfIPjeiVyM/giphy.gif",
        "https://giphy.com/gifs/funny-clown-3o7TKSjRrfIPjeiVyM",
    ],
)
def test_every_giphy_url_of_one_gif_is_one_key(
    url: str,
) -> None:
    assert media_key({"kind": "gif", "url": url}) == "m:giphy:3o7TKSjRrfIPjeiVyM"


def test_every_tenor_rendition_of_one_gif_is_one_key() -> None:
    small = media_key({"url": "https://media.tenor.com/x8v1oNUOmg4AAAAM/clown.gif"})
    large = media_key({"url": "https://media.tenor.com/x8v1oNUOmg4AAAAC/clown.gif"})

    assert small == large == "m:tenor:x8v1oNUOmg4"


def test_a_provider_id_is_used_before_the_url() -> None:
    assert media_key({"provider": "Giphy", "provider_id": "abc", "url": "https://x.y/z.gif"}) == (
        "m:giphy:abc"
    )


def test_a_cdn_url_is_keyed_by_its_path_alone() -> None:
    first = media_key({"url": "https://scontent-lga3-1.xx.fbcdn.net/v/t39/123_n.jpg?_nc=1&oh=a"})
    second = media_key({"url": "https://scontent-iad3-2.xx.fbcdn.net/v/t39/123_n.jpg?_nc=9&oh=b"})
    other = media_key({"url": "https://scontent-lga3-1.xx.fbcdn.net/v/t39/456_n.jpg"})

    assert first == second
    assert first != other
    assert first is not None and first.startswith("m:url:")


def test_media_with_nothing_to_identify_it_has_no_key() -> None:
    assert media_key({"kind": "sticker"}) is None
    assert media_key({"url": "https://example.com/"}) is None


# Content and context keys


def test_content_is_text_and_media_together_in_any_order() -> None:
    assert content_key("t:a", ["m:1", "m:2"]) == content_key("t:a", ["m:2", "m:1"])
    assert content_key("t:a", []) != content_key("t:a", ["m:1"])
    assert content_key(None, ["m:1"]) != content_key("t:a", ["m:1"])
    assert content_key(None, []) is None


def test_context_tells_one_reply_to_two_parents_apart() -> None:
    reply = content_key("t:lol", [])

    assert context_key(reply, "c:hateful", top_level=False) != context_key(
        reply, "c:kind", top_level=False
    )
    assert context_key(reply, None, top_level=True) != context_key(reply, "c:kind", top_level=False)


def test_a_reply_to_an_unknown_parent_or_without_content_has_no_context() -> None:
    assert context_key("c:x", None, top_level=False) is None
    assert context_key(None, "c:y", top_level=False) is None
    assert context_key(None, None, top_level=True) is None
