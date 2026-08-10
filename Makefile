.PHONY: quality test e2e smoke verify-deployment

quality:
	uv sync --frozen
	uv run ruff check src tests
	uv run mypy src
	uv run pytest

test:
	uv run pytest

e2e:
	cd frontend && npm run build && npm run test:e2e

smoke:
	bash scripts/smoke_test.sh http://localhost:8000

verify-deployment:
	bash scripts/verify_core_deployment.sh
