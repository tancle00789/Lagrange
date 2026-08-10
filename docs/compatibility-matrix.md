# Compatibility Matrix

| Component | Phase 1 target |
|---|---|
| Python | 3.12 |
| Node.js | 20.19+ (Vite 8 build requirement) |
| Package manager | uv |
| Web framework | FastAPI + Uvicorn |
| State machine | LangGraph |
| Checkpoint | langgraph-checkpoint-postgres / InMemorySaver for tests |
| Data validation | Pydantic v2 |
| Production identity | OIDC-compatible HTTPS JWKS, RS256/ES256 |
| Database | PostgreSQL 16 |
| Checkpoint tenant isolation | PostgreSQL forced RLS + transaction tenant context |
| Runtime task store | Redis 7 (AOF); in-memory implementation for tests/dev |
| Durable worker delivery | Redis Streams consumer groups, `XAUTOCLAIM` (Redis 6.2+) |
| Live coordination | Redis Pub/Sub + atomic rate/ticket keys; memory for tests/dev |
| Prompt boundary | Untrusted JSONL evidence + static tool routing + citation gate |
| Metrics | Prometheus text exposition and alert rules |
| Tracing | OpenTelemetry SDK + W3C propagation + optional OTLP/HTTP exporter |
| Logging | Correlated JSON stdout with credential redaction |
| Object storage | MinIO/S3 in later phases |
| Test | pytest + pytest-asyncio + FastAPI TestClient |
| Lint/type | ruff + mypy |
| Runtime packaging | Docker Compose |
