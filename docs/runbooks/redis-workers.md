# Redis Worker Operations

## Runtime contract

The default stream is `pk_agent:jobs` and the consumer group is
`pk_agent:workers`; replace `pk_agent` with `PK_AGENT_RUNTIME_KEY_PREFIX` when
configured. Delivery is at least once. A pending entry is healthy while its
owner refreshes the lease and becomes recoverable after
`PK_AGENT_WORKER_LEASE_MS`.

## Inspect the queue

```bash
redis-cli XINFO GROUPS pk_agent:jobs
redis-cli XPENDING pk_agent:jobs pk_agent:workers
redis-cli XINFO CONSUMERS pk_agent:jobs pk_agent:workers
```

Watch `pending`, consumer `idle`, and the oldest pending entry. Sustained
pending growth or entries older than several lease intervals indicates a
worker, Redis connectivity, or downstream provider problem.

## Recovery behavior

1. A graceful shutdown changes the task record to `retrying` and leaves its
   stream entry pending.
2. A hard crash leaves the task's last status unchanged, usually `running`.
3. A healthy worker calls `XAUTOCLAIM` after the lease, increments the persisted
   attempt count, and resumes execution.
4. A terminal result or durable user cancellation causes `XACK`.

Do not manually `XACK` an entry until the corresponding task record has been
checked. Manual acknowledgement discards its automatic recovery path. If Redis
is unavailable, task creation fails rather than pretending the job was queued.

## Configuration guidance

- Keep the lease comfortably above normal event-loop and Redis latency.
- Keep the polling interval low enough for acceptable queue pickup latency.
- Size concurrency for downstream model/provider capacity, not CPU count alone.
- Treat maximum attempts as an error-budget decision; retries can repeat
  idempotent reads but external writes require their own idempotency keys.

