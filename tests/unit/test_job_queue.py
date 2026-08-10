from __future__ import annotations

import asyncio
from typing import cast

from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis

from pk_agent.agentos.job_queue import JobEnvelope, RedisJobQueue, RetryableJobError


def _job(task_id: str = "task-a") -> JobEnvelope:
    return JobEnvelope(
        task_id=task_id,
        initial_state={"task_id": task_id, "input": "research"},
        config={"configurable": {"thread_id": task_id}},
    )


def _queue(client: Redis, *, consumer: str, lease_ms: int = 50) -> RedisJobQueue:
    return RedisJobQueue(
        client,
        key_prefix="queue-test",
        concurrency=1,
        lease_ms=lease_ms,
        block_ms=5,
        retry_backoff_seconds=0,
        consumer_prefix=consumer,
    )


async def _wait_until_acked(client: Redis, queue: RedisJobQueue) -> None:
    await asyncio.sleep(0.01)
    assert (await client.xpending(queue.stream, queue.group))["pending"] == 0


async def test_job_envelope_round_trips_through_stream_and_is_acked() -> None:
    client = cast(Redis, FakeRedis(decode_responses=True))
    queue = _queue(client, consumer="worker-a")
    received: list[JobEnvelope] = []
    completed = asyncio.Event()

    async def handle(job: JobEnvelope) -> None:
        received.append(job)
        completed.set()

    await queue.start(handle)
    await queue.submit(_job())
    await asyncio.wait_for(completed.wait(), timeout=1)

    assert received == [_job()]
    await _wait_until_acked(client, queue)
    await queue.aclose()
    await client.aclose()


async def test_retryable_delivery_retries_before_ack() -> None:
    client = cast(Redis, FakeRedis(decode_responses=True))
    queue = _queue(client, consumer="worker-a")
    attempts = 0
    completed = asyncio.Event()

    async def handle(job: JobEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableJobError("transient")
        completed.set()

    await queue.start(handle)
    await queue.submit(_job())
    await asyncio.wait_for(completed.wait(), timeout=1)

    assert attempts == 3
    await _wait_until_acked(client, queue)
    await queue.aclose()
    await client.aclose()


async def test_unexpected_handler_error_does_not_kill_consumer() -> None:
    client = cast(Redis, FakeRedis(decode_responses=True))
    queue = _queue(client, consumer="worker-a", lease_ms=20)
    attempts = 0
    completed = asyncio.Event()

    async def handle(job: JobEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("unexpected adapter failure")
        completed.set()

    await queue.start(handle)
    await queue.submit(_job())
    await asyncio.wait_for(completed.wait(), timeout=1)

    assert attempts == 2
    assert not queue._workers[0].done()
    await _wait_until_acked(client, queue)
    await queue.aclose()
    await client.aclose()


async def test_idle_delivery_is_claimed_after_worker_crash() -> None:
    client = cast(Redis, FakeRedis(decode_responses=True))
    dead_queue = _queue(client, consumer="dead", lease_ms=20)
    await dead_queue.setup()
    await dead_queue.submit(_job("recovered"))
    delivered = await client.xreadgroup(
        dead_queue.group,
        "dead:0",
        {dead_queue.stream: ">"},
        count=1,
    )
    assert delivered

    await asyncio.sleep(0.03)
    replacement = _queue(client, consumer="replacement", lease_ms=20)
    recovered = asyncio.Event()

    async def handle(job: JobEnvelope) -> None:
        assert job.task_id == "recovered"
        recovered.set()

    await replacement.start(handle)
    await asyncio.wait_for(recovered.wait(), timeout=1)

    await _wait_until_acked(client, replacement)
    await replacement.aclose()
    await client.aclose()


async def test_shutdown_leaves_inflight_delivery_for_lease_takeover() -> None:
    client = cast(Redis, FakeRedis(decode_responses=True))
    first = _queue(client, consumer="first", lease_ms=20)
    started = asyncio.Event()

    async def interrupted(job: JobEnvelope) -> None:
        started.set()
        await asyncio.Event().wait()

    await first.start(interrupted)
    await first.submit(_job("interrupted"))
    await asyncio.wait_for(started.wait(), timeout=1)
    await first.aclose()
    assert (await client.xpending(first.stream, first.group))["pending"] == 1

    await asyncio.sleep(0.03)
    replacement = _queue(client, consumer="replacement", lease_ms=20)
    recovered = asyncio.Event()

    async def handle(job: JobEnvelope) -> None:
        assert job.task_id == "interrupted"
        recovered.set()

    await replacement.start(handle)
    await asyncio.wait_for(recovered.wait(), timeout=1)

    await _wait_until_acked(client, replacement)
    await replacement.aclose()
    await client.aclose()
