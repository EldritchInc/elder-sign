.PHONY: install tools test lint format typecheck check

install:
	python -m pip install -e ".[dev]"

tools:
	python -m elder_sign.build

test:
	python -m pytest

lint:
	python -m ruff check .

format:
	python -m ruff format .

typecheck:
	python -m mypy src/elder_sign tests

check:
	python -m ruff check .
	python -m ruff format --check .
	python -m mypy src/elder_sign tests
	python -m elder_sign.build --check
	python -m pytest
