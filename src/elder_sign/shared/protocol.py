"""How an Elder Sign tool talks to Azathoth: one JSON object in, one JSON object out."""

import json
import sys
from collections.abc import Callable
from typing import Any

JsonObject = dict[str, Any]
"""A JSON object, as a tool receives its inputs and returns its outputs."""


def main(
    run: Callable[[JsonObject], JsonObject],
) -> None:
    """Read the tool's inputs from standard input, run it, and write its outputs."""

    inputs = json.load(sys.stdin)

    if not isinstance(inputs, dict):
        raise SystemExit("An Elder Sign tool takes one JSON object as its inputs.")

    json.dump(run(inputs), sys.stdout, ensure_ascii=False, sort_keys=True)
