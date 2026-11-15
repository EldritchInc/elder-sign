"""Modules the tools share, inlined into each tool's source when it is built.

Every module here is standard library only, imports other shared modules only
as `from elder_sign.shared.<module> import <name>` without aliases, and is
listed in `SHARED_MODULES` after every shared module it imports.
"""

SHARED_MODULES = (
    "elder_sign.shared.protocol",
    "elder_sign.shared.keys",
    "elder_sign.shared.store",
)
"""Every shared module, in the order they are inlined."""
