"""Tests proving a fetched GIF, sticker or image becomes a still Azathoth accepts."""

import io

import pytest
from PIL import Image

from elder_sign.media import MAX_SIDE, still_of
from elder_sign.platforms.base import ACCEPTED_MEDIA_TYPES


def encoded(
    image: Image.Image,
    format: str,
    **options: object,
) -> bytes:
    """Return an image encoded in a format."""

    out = io.BytesIO()
    image.save(out, format=format, **options)

    return out.getvalue()


def animated(
    format: str,
) -> bytes:
    """Return a two-frame animation, red then blue."""

    red = Image.new("RGB", (40, 30), (255, 0, 0))
    blue = Image.new("RGB", (40, 30), (0, 0, 255))

    return encoded(red, format, save_all=True, append_images=[blue], duration=100, loop=0)


def opened(
    content: bytes,
) -> Image.Image:
    """Return an encoded image, loaded."""

    image = Image.open(io.BytesIO(content))
    image.load()

    return image


def test_a_gifs_first_frame_becomes_a_png() -> None:
    still = still_of(animated("GIF"))

    assert still is not None
    assert still.media_type == "image/png"

    image = opened(still.content)

    assert image.format == "PNG"
    assert image.size == (40, 30)
    assert image.convert("RGB").getpixel((5, 5)) == (255, 0, 0)


def test_a_still_gif_becomes_a_png_too() -> None:
    still = still_of(encoded(Image.new("RGB", (8, 8), (0, 255, 0)), "GIF"))

    assert still is not None
    assert still.media_type == "image/png"


def test_an_animated_webps_first_frame_becomes_a_png() -> None:
    still = still_of(animated("WEBP"))

    assert still is not None
    assert still.media_type == "image/png"
    pixel = opened(still.content).convert("RGB").getpixel((5, 5))

    assert isinstance(pixel, tuple)
    assert pixel[0] > 200 and pixel[2] < 50


@pytest.mark.parametrize(
    ("format", "media_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_a_small_still_in_an_accepted_type_is_kept_as_it_is(
    format: str,
    media_type: str,
) -> None:
    content = encoded(Image.new("RGB", (64, 48), (10, 20, 30)), format)

    still = still_of(content)

    assert still is not None
    assert still.content == content
    assert still.media_type == media_type
    assert still.media_type in ACCEPTED_MEDIA_TYPES


def test_a_large_image_is_shrunk_to_fit_keeping_its_shape() -> None:
    content = encoded(Image.new("RGB", (MAX_SIDE * 3, MAX_SIDE), (10, 20, 30)), "JPEG")

    still = still_of(content)

    assert still is not None
    assert still.media_type == "image/png"
    assert opened(still.content).size == (MAX_SIDE, MAX_SIDE // 3)


def test_an_image_too_heavy_even_when_shrunk_is_refused() -> None:
    content = encoded(Image.new("RGB", (64, 64), (10, 20, 30)), "PNG")

    assert still_of(content, max_bytes=10) is None


@pytest.mark.parametrize(
    "content",
    [b"", b"not an image", b"\x00\x00\x00\x18ftypmp42" + bytes(64), b"GIF89a\x01"],
)
def test_what_is_not_an_image_is_refused(
    content: bytes,
) -> None:
    assert still_of(content) is None


def test_an_image_claiming_more_pixels_than_is_safe_is_refused() -> None:
    content = encoded(Image.new("1", (20000, 20000)), "PNG")

    assert still_of(content, max_pixels=1_000_000) is None
