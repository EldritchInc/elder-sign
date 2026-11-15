"""Make a GIF, sticker or image a comment posted into a still that Azathoth accepts.

`judge-media` looks at one image a comment posted, and Azathoth accepts a
workflow's image only as PNG, JPEG or WebP (ADR 0112). A GIF is judged by
its first frame, which is what a person sees before it plays, and so is an
animated WebP. A still already in an accepted type, small enough, is kept
byte for byte, so the same image is always the same image. Anything else
becomes a PNG, shrunk to fit `MAX_SIDE` with its shape kept.

What is fetched comes from strangers. Nothing that is not an image is
accepted, and an image claiming more pixels than `MAX_PIXELS` is refused
before it is decoded, so a crafted file cannot make Elder Sign decode a
billion pixels.
"""

import io
import warnings
from typing import Final

from elder_sign.platforms.base import FetchedMedia

MAX_SIDE: Final = 1024
"""The longest side, in pixels, of a still a model is shown."""

MAX_STILL_BYTES: Final = 4 * 1024 * 1024
"""The heaviest still a model is shown."""

MAX_PIXELS: Final = 50_000_000
"""The most pixels an image may claim before it is decoded at all."""

KEPT_AS_THEY_ARE: Final = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
"""The formats Azathoth accepts, by Pillow's name for them, and the type each is sent as."""


def still_of(
    content: bytes,
    *,
    max_side: int = MAX_SIDE,
    max_bytes: int = MAX_STILL_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> FetchedMedia | None:
    """Return one GIF's first frame or one image as a still Azathoth accepts, or none.

    None is returned for what is not an image, for an image claiming more
    than `max_pixels` pixels, and for a still heavier than `max_bytes` even
    once shrunk.
    """

    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(io.BytesIO(content)) as image:
                (width, height) = image.size

                if width * height > max_pixels:
                    return None

                image.seek(0)
                media_type = KEPT_AS_THEY_ARE.get(image.format or "")
                moving = bool(getattr(image, "is_animated", False))

                if (
                    media_type is not None
                    and not moving
                    and max(width, height) <= max_side
                    and len(content) <= max_bytes
                ):
                    image.load()
                    return FetchedMedia(content=content, media_type=media_type)

                frame = image.convert("RGBA")
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
        EOFError,
        SyntaxError,
    ):
        return None

    frame.thumbnail((max_side, max_side))
    out = io.BytesIO()
    frame.save(out, format="PNG", optimize=True)
    still = out.getvalue()

    if len(still) > max_bytes:
        return None

    return FetchedMedia(content=still, media_type="image/png")
