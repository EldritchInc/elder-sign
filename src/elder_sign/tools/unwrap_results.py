"""Turn what `map_workflow` collected into the list of what each child returned.

`map_workflow` returns each child's whole emitted result, in item order. A
result that is an object of one `result` key is the child's value wrapped as
`run_workflow` returns it, and is unwrapped; any other result is the value
itself. A child that returned nothing has no place in the list.
"""

from typing import Any, Final

from elder_sign.shared.protocol import JsonObject

TOOL_ID: Final = "e1de7516-0000-4000-8000-000000000090"

IMPLEMENTATION_ID: Final = "e1de7516-0000-4000-8000-000000000091"

NAME: Final = "unwrap_results"

VERSION: Final = "1.0.0"

DESCRIPTION: Final = (
    "Turn the results map_workflow collected into the list of the values its children "
    "returned, in item order."
)

INPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"results": {"type": "array"}},
    "required": ["results"],
}

OUTPUT_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"items": {"type": "array"}},
    "required": ["items"],
}

TEST_CASES: Final[tuple[Any, ...]] = (
    (
        "unwraps each result",
        "Wrapped and bare results come back as values, in order, and empty ones are dropped.",
        {"results": [{"result": 1}, {"verdict": "hostile"}, None, {"result": {"a": 2}}]},
        {"items": [1, {"verdict": "hostile"}, {"a": 2}]},
    ),
    ("unwraps nothing", "A map of no items has no results.", {"results": []}, {"items": []}),
)


def _unwrapped(
    result: Any,
) -> Any:
    """Return the value one child returned."""

    if isinstance(result, dict) and set(result) == {"result"}:
        return result["result"]

    return result


def run(
    inputs: JsonObject,
) -> JsonObject:
    """Return the value each child returned, in item order."""

    return {
        "items": [
            value
            for value in (_unwrapped(result) for result in inputs["results"])
            if value is not None
        ]
    }
