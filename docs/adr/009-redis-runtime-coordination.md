# ADR 009 — Redis Runtime Coordination Across API Replicas

Status: accepted (Phase 06B)
Date: 2026-08-05

## Context

Phase 06 moved task and idempotency records to Redis, but live coordination
remained process-local: rate limits, SSE tickets, event subscriptions and
coroutine cancellation. Multiple API replicas could therefore exceed the
configured aggregate rate, could not serve one another's SSE connections, and
could not immediately stop work owned by another process.

## Decision

- Redis runtime mode now selects Redis implementations for all four live
  coordination concerns. Memory implementations remain available for isolated
  tests and single-process development.
- Rate limiting uses a tenant-hashed, one-second fixed window and an atomic
  Redis transaction. The configured RPS is therefore enforced across replicas.
- SSE tickets are stored with Redis TTL and consumed with `GETDEL`. A ticket
  issued by one replica can establish an event stream on another and cannot be
  replayed.
- Agent events use task-scoped Redis Pub/Sub channels. Task state remains the
  durable source of truth; SSE sends a persisted snapshot before subscribing.
- Every Redis-backed API process subscribes to a cancellation control channel.
  A cancel request first commits the shared terminal state and then publishes
  the task ID. The process owning that `asyncio.Task` calls `cancel()`.

## Consequences

- API replicas now share rate budgets, tickets, live events and cancellation
  control without sticky routing.
- Pub/Sub is intentionally ephemeral. A disconnected client reconstructs state
  from the task snapshot/list API; Redis task records, not events, are durable.
- Cancellation remains safe if a control message is lost because the terminal
  Redis transition rejects late result writes. The owner may continue consuming
  compute until its current operation returns if it missed the message.
- This is not a durable worker queue. API process loss marks locally interrupted
  work failed; automatic lease/retry/reassignment remains a later phase.
