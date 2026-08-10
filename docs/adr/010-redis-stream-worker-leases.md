# ADR 010 — Durable Redis Streams Worker Delivery

Status: accepted (Phase 07)
Date: 2026-08-05

## Context

Redis-backed task records made state durable, and Pub/Sub made live control
cross-replica, but execution still depended on a coroutine created by the API
process that accepted the request. Process loss could therefore leave work
without an owner. Pub/Sub cannot repair this because it does not retain an
unconsumed delivery.

## Decision

- Redis runtime mode submits a compact `JobEnvelope` to a namespaced Redis
  Stream. A consumer group distributes jobs across the worker loops embedded
  in all API replicas. The envelope contains only task ID, graph input and
  LangGraph configuration; documents and retrieved content remain outside the
  queue.
- Delivery is at least once. A worker acknowledges with `XACK` only after the
  execution contract reaches a terminal outcome or observes a durable user
  cancellation.
- While a graph runs, the owner periodically refreshes the pending-entry idle
  time with `XCLAIM`. Another worker uses `XAUTOCLAIM` after the configured
  lease expires, which recovers work after process loss.
- Graceful shutdown cancels in-flight execution, changes `running` to
  `retrying`, and deliberately leaves the stream entry unacknowledged. A new
  owner resumes it after lease expiry.
- Execution attempts are persisted on the task record. Transient failures move
  `running -> retrying`; after `PK_AGENT_WORKER_MAX_ATTEMPTS`, the task moves to
  `failed` and the delivery is acknowledged.
- Worker concurrency, lease, polling interval, retry delay and maximum attempts
  are configuration values. Memory runtime retains the local execution path
  for tests and single-process development.

## Consequences

- API acceptance is no longer the lifetime boundary for a task. Any healthy
  replica can execute or recover it without sticky routing.
- Handlers must be idempotent because a crash can occur after an external side
  effect but before `XACK`. Existing conditional task transitions prevent a
  recovered delivery from reversing `cancelled`, `completed` or `failed`.
- The queue uses short non-blocking reads followed by async sleep. This avoids
  event-loop blocking in compatible Redis test doubles and bounds pickup
  latency by `PK_AGENT_WORKER_BLOCK_MS`.
- Redis AOF improves durability but is not exactly-once execution. Operators
  monitor the pending-entry list and reclaim latency using the worker runbook.

