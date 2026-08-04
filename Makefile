.PHONY: quality test smoke

quality:
	uv sync --frozen
	uv run ruff check src tests
	uv run mypy src
	uv run pytest

test:
	uv run pytest

smoke:
	bash scripts/smoke_test.sh http://localhost:8000
