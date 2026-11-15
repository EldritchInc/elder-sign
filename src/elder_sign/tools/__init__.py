"""The Elder Sign tools, each an ordinary Python module built into an Azathoth tool document.

A tool module declares its document as module constants and its behavior as
`run`:

- `TOOL_ID` and `IMPLEMENTATION_ID`: the UUIDs of its definition and its one
  process implementation;
- `NAME`, `VERSION` and `DESCRIPTION`;
- `INPUT_SCHEMA` and `OUTPUT_SCHEMA`: JSON Schemas;
- optionally `ENVIRONMENT`, the variables it is granted (ADR 0099), and
  `TEST_CASES`, as `(name, description, inputs, expected_output)` tuples;
- `run(inputs) -> outputs`.
"""

TOOL_MODULES: tuple[str, ...] = ()
"""Every tool module, by import name."""
