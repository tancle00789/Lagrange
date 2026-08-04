# Phase 00 Build Report

## Delivered
- Compatibility matrix: `docs/compatibility-matrix.md`
- ADR 001: LangGraph as the only task state machine
- ADR 002: PostgreSQL checkpointing
- ADR 003: KnowledgeProvider abstraction over WeKnora
- Threat model draft: `docs/threat_model.md`

## Decisions
- AgentOS/FastAPI is the control plane only.
- LangGraph is the only runtime state machine.
- WeKnora is accessed through a provider interface, not direct imports.
- Phase 1 uses FakeModel and does not call real model providers.

## Verification
Passed with Phase 01 quality gate: `uv sync --frozen`, `ruff check src tests`, `mypy src`, `pytest`, `docker compose up -d --build`, `scripts/smoke_test.sh`.
