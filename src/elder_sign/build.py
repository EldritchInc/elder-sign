"""Build the Azathoth tool documents from the tool modules.

A process tool runs as one script, so each tool's source is assembled from
its module and every shared module it imports, directly or through another
shared module. The imports of shared modules are removed, because their
names are then defined in the same script. The document's implementation
digest covers exactly the script that runs.

    python -m elder_sign.build          # write every document
    python -m elder_sign.build --check  # fail if any document is stale
"""

import argparse
import ast
import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid5

from azathoth.tools import (
    ToolDefinition,
    ToolDocument,
    ToolImplementation,
    ToolInputSchema,
    ToolOutputSchema,
    ToolTestCase,
    encode_tool_document,
)

from elder_sign.shared import SHARED_MODULES
from elder_sign.tools import TOOL_MODULES

DOCUMENTS = Path(__file__).parent / "documents" / "tools"
"""Where the built tool documents live, inside the package so they ship with it."""

PROTOCOL_MODULE = "elder_sign.shared.protocol"
"""The shared module every tool includes, for the `main` its script ends by calling."""

SHARED_PREFIX = "elder_sign.shared."

PACKAGE = "elder_sign"

PROCESS_COMMAND = ("python",)
"""The command that runs each tool's script."""

TIMEOUT_SECONDS = 60.0
"""How long one tool's script may run."""


class BuildError(ValueError):
    """Raised when a tool or shared module cannot be built into a document."""


def _module_source(
    name: str,
) -> str:
    """Return the source of one module, by import name."""

    spec = importlib.util.find_spec(name)

    if spec is None or spec.origin is None:
        raise BuildError(f"Module {name} cannot be found.")

    return Path(spec.origin).read_text(encoding="utf-8")


def _split_source(
    name: str,
    source: str,
) -> tuple[str, tuple[str, ...], str]:
    """Split a module's source into its docstring, the shared modules it imports, and its body.

    The body is the source without the docstring and without the imports of
    shared modules.

    Raises:
        BuildError: The module imports from Elder Sign other than a shared
            module by name, imports with an alias, or imports from the future.
    """

    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    removed: set[int] = set()
    docstring = ""
    shared: list[str] = []

    first = tree.body[0] if tree.body else None

    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        span = range(first.lineno - 1, first.end_lineno or first.lineno)
        docstring = "".join(lines[index] for index in span)
        removed.update(span)

    top_level = set(map(id, tree.body))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            raise BuildError(f"{name} imports from __future__, which cannot be inlined.")

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}."):
                    raise BuildError(
                        f"{name} imports {alias.name} as a module; import its names with "
                        "`from ... import ...` instead."
                    )

        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue

        if node.module != PACKAGE and not node.module.startswith(f"{PACKAGE}."):
            continue

        if not node.module.startswith(SHARED_PREFIX):
            raise BuildError(
                f"{name} imports {node.module}; a tool may import only shared modules."
            )

        if id(node) not in top_level:
            raise BuildError(f"{name} imports {node.module} below the top level of the module.")

        if any(alias.asname is not None for alias in node.names):
            raise BuildError(f"{name} imports from {node.module} with an alias.")

        shared.append(node.module)
        removed.update(range(node.lineno - 1, node.end_lineno or node.lineno))

    body = "".join(line for index, line in enumerate(lines) if index not in removed)

    return docstring, tuple(shared), body.strip("\n") + "\n"


def _shared_closure(
    imported: Sequence[str],
) -> tuple[str, ...]:
    """Return the shared modules a tool needs, with the protocol, in inlining order.

    Raises:
        BuildError: A shared module is not listed in `SHARED_MODULES`, or is
            listed before a shared module it imports.
    """

    needed: set[str] = set()
    pending = [PROTOCOL_MODULE, *imported]

    while pending:
        name = pending.pop()

        if name in needed:
            continue

        if name not in SHARED_MODULES:
            raise BuildError(f"Shared module {name} is not listed in SHARED_MODULES.")

        needed.add(name)
        _, dependencies, _ = _split_source(name, _module_source(name))

        for dependency in dependencies:
            if SHARED_MODULES.index(dependency) > SHARED_MODULES.index(name):
                raise BuildError(
                    f"Shared module {name} imports {dependency}, which SHARED_MODULES lists "
                    "after it."
                )

        pending.extend(dependencies)

    return tuple(name for name in SHARED_MODULES if name in needed)


def assemble_source(
    module: ModuleType,
) -> str:
    """Return the one script a tool runs as: its docstring, its shared modules, its body."""

    name = module.__name__
    docstring, imported, body = _split_source(name, _module_source(name))
    parts = [docstring.strip("\n") + "\n"] if docstring else []

    for shared in _shared_closure(imported):
        _, _, shared_body = _split_source(shared, _module_source(shared))
        parts.append(f"# --- {shared} ---\n\n{shared_body}")

    parts.append(f"# --- {name} ---\n\n{body}")
    parts.append('if __name__ == "__main__":\n    main(run)\n')

    return "\n\n".join(parts)


def _attribute(
    module: ModuleType,
    name: str,
    kind: type,
    default: Any = None,
) -> Any:
    """Return one declared constant of a tool module, checking its type."""

    value = getattr(module, name, default)

    if not isinstance(value, kind):
        raise BuildError(f"{module.__name__}.{name} must be a {kind.__name__}.")

    return value


def build_document(
    module: ModuleType,
) -> ToolDocument:
    """Build the tool document a tool module declares."""

    if not callable(getattr(module, "run", None)):
        raise BuildError(f"{module.__name__} declares no run function.")

    tool_id = UUID(_attribute(module, "TOOL_ID", str))
    version = _attribute(module, "VERSION", str)

    definition = ToolDefinition(
        id=tool_id,
        name=_attribute(module, "NAME", str),
        description=_attribute(module, "DESCRIPTION", str),
        version=version,
        input_schema=ToolInputSchema(json_schema=_attribute(module, "INPUT_SCHEMA", dict)),
        output_schema=ToolOutputSchema(json_schema=_attribute(module, "OUTPUT_SCHEMA", dict)),
    )

    implementation = ToolImplementation(
        id=UUID(_attribute(module, "IMPLEMENTATION_ID", str)),
        tool_id=tool_id,
        tool_version=version,
        version=version,
        runtime="process",
        entrypoint="run",
        source=assemble_source(module),
        command=PROCESS_COMMAND,
        timeout_seconds=TIMEOUT_SECONDS,
        environment=_attribute(module, "ENVIRONMENT", tuple, ()),
    )

    test_cases = tuple(
        ToolTestCase(
            id=uuid5(tool_id, case_name),
            tool_id=tool_id,
            name=case_name,
            description=description,
            inputs=inputs,
            expected_output=expected_output,
        )
        for case_name, description, inputs, expected_output in _attribute(
            module, "TEST_CASES", tuple, ()
        )
    )

    return ToolDocument(
        definition=definition,
        implementations=(implementation,),
        test_cases=test_cases,
    )


def document_path(
    document: ToolDocument,
    directory: Path = DOCUMENTS,
) -> Path:
    """Return where one tool's document lives: its name, with hyphens."""

    return directory / f"{document.definition.name.replace('_', '-')}.json"


def encoded_documents(
    modules: Sequence[str] = TOOL_MODULES,
    directory: Path = DOCUMENTS,
) -> dict[Path, str]:
    """Return every tool's document, encoded as it is written, by its path."""

    documents: dict[Path, str] = {}

    for name in modules:
        document = build_document(importlib.import_module(name))
        path = document_path(document, directory)

        if path in documents:
            raise BuildError(f"Two tools build {path.name}.")

        documents[path] = encode_tool_document(document) + "\n"

    return documents


def stale_documents(
    modules: Sequence[str] = TOOL_MODULES,
    directory: Path = DOCUMENTS,
) -> list[str]:
    """Return a line for every document that is missing, out of date, or built by no tool."""

    documents = encoded_documents(modules, directory)
    problems = [
        f"{path.name} is missing"
        if not path.exists()
        else f"{path.name} is out of date with its source"
        for path, encoded in documents.items()
        if not path.exists() or path.read_text(encoding="utf-8") != encoded
    ]
    problems.extend(
        f"{path.name} is built by no tool"
        for path in sorted(directory.glob("*.json"))
        if path not in documents
    )

    return problems


def write_documents(
    modules: Sequence[str] = TOOL_MODULES,
    directory: Path = DOCUMENTS,
) -> list[Path]:
    """Write every tool's document, returning the paths that changed."""

    directory.mkdir(parents=True, exist_ok=True)
    changed = []

    for path, encoded in encoded_documents(modules, directory).items():
        if not path.exists() or path.read_text(encoding="utf-8") != encoded:
            path.write_text(encoded, encoding="utf-8")
            changed.append(path)

    return changed


def main(
    arguments: Sequence[str] | None = None,
) -> int:
    """Build the tool documents, or with `--check` report the stale ones."""

    parser = argparse.ArgumentParser(prog="python -m elder_sign.build", description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if any document is stale")
    options = parser.parse_args(arguments)

    if options.check:
        problems = stale_documents()

        for problem in problems:
            print(f"stale: {problem}", file=sys.stderr)

        if problems:
            print("Run `make tools` to rebuild them.", file=sys.stderr)

        return 1 if problems else 0

    for path in write_documents():
        print(f"built {path.name}")

    for problem in stale_documents():
        print(f"warning: {problem}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
