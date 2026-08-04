# Compatibility Matrix

| Component | Phase 1 target |
|---|---|
| Python | 3.12 |
| Package manager | uv |
| Web framework | FastAPI + Uvicorn |
| State machine | LangGraph |
| Checkpoint | langgraph-checkpoint-postgres / InMemorySaver for tests |
| Data validation | Pydantic v2 |
| Database | PostgreSQL 16 |
| Cache / rate limit | Redis 7 in target stack; in-memory limiter in Phase 1 |
| Object storage | MinIO/S3 in later phases |
| Test | pytest + pytest-asyncio + FastAPI TestClient |
| Lint/type | ruff + mypy |
| Runtime packaging | Docker Compose |
