# Phase 07 Build Report — Durable Worker Leases and Recovery

Date: 2026-08-05

## Scope delivered

- Added a Redis Streams consumer-group queue for Redis runtime mode.
- Added compact job envelopes, configurable worker concurrency, bounded
  persisted attempts, retry state, heartbeat lease renewal, `XAUTOCLAIM`
  takeover and terminal `XACK` semantics.
- Changed graceful shutdown from terminal failure to recoverable `retrying`;
  hard process loss is recovered from the pending-entry list after lease expiry.
- Kept user cancellation terminal across replicas and prevented recovered
  deliveries from overwriting terminal task state.
- Added worker error containment so an unexpected delivery failure leaves the
  entry recoverable without permanently killing the consumer loop.
- Added ADR 010 and a Redis worker operations runbook.

## Verification

| Gate | Result |
|---|---|
| `ruff check --no-cache src tests` | ok |
| `mypy src` | ok, 49 source files |
| Queue/execution/coordination focused tests | 12 passed |
| Full pytest outside restricted WSL sandbox | 92 passed |

Focused coverage proves stream round-trip and ACK, transient retries, maximum
attempt failure, idle-entry crash recovery, shutdown takeover, consumer survival
after unexpected errors, and cross-replica cancellation with shared workers.

## Explicit boundary

The runtime provides at-least-once delivery, not exactly-once side effects.
External-write tools must supply their own idempotency contract. Real Redis and
container deployment remain part of the final environment verification phase.
