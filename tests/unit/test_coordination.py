from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from fastapi import HTTPException
from redis.asyncio import Redis

from pk_agent.agentos import main as main_module
from pk_agent.agentos.api.v1.tasks import cancel_task, create_task
from pk_agent.agentos.cancellation import RedisCancellationCoordinator
from pk_agent.agentos.dependencies import RedisRateLimiter
from pk_agent.agentos.main import create_app
from pk_agent.agentos.task_store import RedisTaskStore
from pk_agent.config import Settings
from pk_agent.harness.bus import RedisAgentEventBus
from pk_agent.models.io import TaskCreateRequest
from pk_agent.security.sse import RedisSseTicketStore
from pk_agent.security.tenant import Principal


def _clients() -> tuple[Redis, Redis]:
    server = FakeServer()
    first = FakeRedis(server=server, decode_responses=True)
    second = FakeRedis(server=server, decode_responses=True)
    return cast(Redis, first), cast(Redis, second)


async def test_redis_rate_limit_is_shared_across_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pk_agent.agentos.dependencies.time.time", lambda: 100.0)
    first, second = _clients()
    limiter_a = RedisRateLimiter(first, 2, key_prefix="test")
    limiter_b = RedisRateLimiter(second, 2, key_prefix="test")

    await limiter_a.check("tenant-a")
    await limiter_b.check("tenant-a")
    with pytest.raises(HTTPException) as exc_info:
        await limiter_a.check("tenant-a")
    assert exc_info.value.status_code == 429
    await first.aclose()
    await second.aclose()


async def test_redis_sse_ticket_can_be_consumed_once_on_another_instance() -> None:
    first, second = _clients()
    issuer = RedisSseTicketStore(first, ttl_seconds=30, key_prefix="test")
    consumer = RedisSseTicketStore(second, ttl_seconds=30, key_prefix="test")
    principal = Principal("tenant-a", "user-1", frozenset({"admin"}))

    token = await issuer.issue(principal, "task-a")
    ticket = await consumer.consume(token, "task-a")

    assert ticket is not None and ticket.tenant_id == "tenant-a"
    assert await issuer.consume(token, "task-a") is None
    await first.aclose()
    await second.aclose()


async def test_redis_event_bus_delivers_across_instances() -> None:
    first, second = _clients()
    publisher = RedisAgentEventBus(first, key_prefix="test")
    subscriber = RedisAgentEventBus(second, key_prefix="test")

    async def receive_one() -> dict[str, Any]:
        async for event in subscriber.subscribe("task-a"):
            return event
        raise AssertionError("event stream ended")

    pending = asyncio.create_task(receive_one())
    await asyncio.sleep(0.01)
    await publisher.publish("task-a", {"type": "completed", "value": 7})

    assert await asyncio.wait_for(pending, timeout=1) == {"type": "completed", "value": 7}
    await first.aclose()
    await second.aclose()


async def test_redis_cancellation_reaches_owner_instance() -> None:
    first, second = _clients()
    owner = RedisCancellationCoordinator(first, key_prefix="test")
    requester = RedisCancellationCoordinator(second, key_prefix="test")
    stopped = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    run = asyncio.create_task(work())
    await owner.start(lambda task_id: run.cancel() if task_id == "task-a" else None)
    await requester.request("task-a")

    await asyncio.wait_for(stopped.wait(), timeout=1)
    assert run.cancelled()
    await owner.aclose()
    await requester.aclose()
    await first.aclose()
    await second.aclose()


async def test_cancel_endpoint_reaches_graph_on_another_app_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _clients()
    store_a = RedisTaskStore(first, key_prefix="test")
    store_b = RedisTaskStore(second, key_prefix="test")
    settings = Settings(
        database_url="memory://",
        runtime_store="redis",
        runtime_key_prefix="test",
        rate_limit_rps=1000,
        worker_block_ms=10,
        worker_lease_ms=100,
    )
    app_a = create_app(settings, literature_sources={})
    app_b = create_app(settings, literature_sources={})
    principal = Principal("tenant-a", "user-1", frozenset({"admin"}))
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingGraph:
        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    monkeypatch.setattr(main_module, "_build_task_store", lambda _settings: store_a)
    async with app_a.router.lifespan_context(app_a):
        monkeypatch.setattr(main_module, "_build_task_store", lambda _settings: store_b)
        async with app_b.router.lifespan_context(app_b):
            graph = BlockingGraph()
            app_a.state.graph = graph
            app_b.state.graph = graph
            created = await create_task(
                TaskCreateRequest(input="cross replica"),
                SimpleNamespace(app=app_a),
                principal,
                None,
            )
            await asyncio.wait_for(started.wait(), timeout=1)

            response = await cancel_task(
                created.task_id,
                SimpleNamespace(app=app_b),
                principal,
            )
            await asyncio.wait_for(stopped.wait(), timeout=1)

            record = await app_b.state.task_store.get(created.task_id)
            assert response.status == "cancelled"
            assert record is not None and record["status"] == "cancelled"
