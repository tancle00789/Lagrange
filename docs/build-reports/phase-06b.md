# Phase 06B Build Report — Distributed Runtime Coordination

Date: 2026-08-05

## Scope delivered

- Added Redis-backed tenant rate limiting shared across API replicas.
- Moved SSE tickets to Redis TTL keys with atomic `GETDEL` consumption.
- Added task-scoped Redis Pub/Sub event delivery for cross-replica SSE.
- Added a Redis cancellation control listener to every runtime replica. A
  request routed to app B now cancels the graph coroutine owned by app A after
  committing the shared `cancelled` state.
- Generalized the graph against an `EventBus` protocol while retaining memory
  implementations for tests and local development.
- Added ADR 009 and five cross-instance coordination tests.

## Verification

| Gate | Result |
|---|---|
| `ruff check src tests` | ok |
| `mypy src` | ok, 48 source files |
| Focused distributed coordination tests | 5 passed |
| Full pytest outside restricted WSL sandbox | 85 passed |

## Explicit boundary

Redis Pub/Sub provides live coordination, not durable execution. Task state is
safe if a message is missed, but automatic worker reassignment after process
loss still requires a durable queue/lease model.
