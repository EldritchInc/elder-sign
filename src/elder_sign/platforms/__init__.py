"""The social platforms Elder Sign reads a person's own posts from."""

from elder_sign.platforms.base import (
    ACCEPTED_MEDIA_TYPES,
    Comment,
    FetchedMedia,
    MediaItem,
    Platform,
    Post,
    Scan,
    ScanError,
    fingerprint_of,
    validate_scan,
)
from elder_sign.platforms.fake import FakePlatform

__all__ = [
    "ACCEPTED_MEDIA_TYPES",
    "Comment",
    "FakePlatform",
    "FetchedMedia",
    "MediaItem",
    "Platform",
    "Post",
    "Scan",
    "ScanError",
    "fingerprint_of",
    "validate_scan",
]
