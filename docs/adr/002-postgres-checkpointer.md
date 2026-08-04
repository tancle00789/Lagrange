# ADR 002: PostgreSQL checkpointing

## Status
Accepted for Phase 1.

## Context
Phase 1 must prove durable run state. The target production stack includes PostgreSQL.

## Decision
Use `langgraph-checkpoint-postgres` with `AsyncPostgresSaver`. `thread_id` is `{tenant_id}:{task_id}`; `checkpoint_ns` comes from `PK_AGENT_CHECKPOINT_NS`.

## Consequences
- Local tests can still use `InMemorySaver` by setting `PK_AGENT_DATABASE_URL=memory://`.
- Docker Compose smoke test validates real PostgreSQL checkpoint setup.
- Production hardening still needs tenant RLS or schema-per-tenant.
