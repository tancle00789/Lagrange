# ADR 008 — Durable Task Control Plane and Cancellation Semantics

Status: accepted (Phase 06)
Date: 2026-08-05

## Context

Task records and idempotency mappings lived in process-local dictionaries.
Restarting the API erased task visibility, and separate replicas could not see
one another's records. `DELETE /tasks/{id}` changed the displayed status but
did not cancel the `asyncio.Task` running LangGraph, so a late graph result
could overwrite `cancelled` with `completed`.

LangGraph checkpoints were already durable in PostgreSQL, but the HTTP control
plane had no durable index connecting tenants to those executions.

## Decision

- Introduce an asynchronous `TaskStore` boundary with memory and Redis
  implementations. Memory remains the test/single-process default; production
  startup requires `PK_AGENT_RUNTIME_STORE=redis`.
- Redis stores bounded JSON task records, a per-tenant sorted task index, and
  SHA-256-hashed tenant/idempotency mappings. Task retention defaults to seven
  days and idempotency retention to one day.
- Status writes support expected-source-state checks using a lock in memory and
  Redis WATCH transactions. A graph result can write only from `running`; it
  cannot overwrite a concurrent terminal state.
- Each API process tracks the `asyncio.Task` objects it owns. Cancellation first
  atomically transitions active state to `cancelled`, then calls `cancel()` on
  the locally-owned coroutine. Shutdown cancels owned work and records an
  interrupted run as failed instead of leaving it indefinitely running.
- Docker Compose gets a dedicated append-only Redis service for pk-agent. It is
  separate from WeKnora's private Redis subsystem.

## Consequences

- Task listing, status and idempotent creation survive API restarts for the
  configured retention period.
- Local cancellation now stops model/retrieval awaits and cannot be reversed by
  a late completion write.
- If a cancel request reaches a different replica, shared state still becomes
  `cancelled` and rejects late completion, but the owning replica cannot yet be
  signalled to release compute immediately. A shared worker queue/control
  channel is required for full distributed cancellation.
- Redis currently stores small task/report metadata only. Documents and PDFs
  remain owned by WeKnora; graph checkpoints remain owned by PostgreSQL.
