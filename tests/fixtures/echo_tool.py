"""Shout a message back: a tool the build tests build and run."""

from typing import Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-00000000f001"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-00000000f002"

NAME: Final = "echo_shout"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = "Return the message, upper-cased."

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"echo": {"type": "string"}},
    "required": ["echo"],
}

ENVIRONMENT: Final = ("ECHO_SETTING",)

TEST_CASES: Final = (("shouts", "Upper-cases the message.", {"message": "hi"}, {"echo": "HI"}),)


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the message, upper-cased."""

    return {"echo": str(inputs["message"]).upper()}
