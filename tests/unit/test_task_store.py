from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis

from pk_agent.agentos.api.v1.tasks import cancel_task, create_task
from pk_agent.agentos.main import create_app
from pk_agent.agentos.task_store import InMemoryTaskStore, RedisTaskStore
from pk_agent.config import Settings
from pk_agent.models.io import TaskCreateRequest
from pk_agent.security.tenant import Principal


def _record(task_id: str, *, status: str = "pending") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "thread_id": f"tenant-a:{task_id}",
        "status": status,
        "created_at": time.time(),
    }


async def test_memory_store_claims_idempotency_atomically() -> None:
    store = InMemoryTaskStore()
    first, second = await asyncio.gather(
        store.create(_record("task-a"), idempotency_key="same"),
        store.create(_record("task-b"), idempotency_key="same"),
    )
    assert sum(created for _, created in (first, second)) == 1
    assert first[0]["task_id"] == second[0]["task_id"]


async def test_expected_status_prevents_cancelled_task_overwrite() -> None:
    store = InMemoryTaskStore()
    await store.create(_record("task-a", status="running"))
    cancelled = await store.update("task-a", {"status": "cancelled"}, expected_statuses={"running"})
    completed = await store.update("task-a", {"status": "completed"}, expected_statuses={"running"})
    assert cancelled is not None and cancelled["status"] == "cancelled"
    assert completed is not None and completed["status"] == "cancelled"


async def test_redis_store_scopes_idempotency_and_lists_newest_first() -> None:
    client = FakeRedis(decode_responses=True)
    store = RedisTaskStore(cast(Redis, client), key_prefix="test")
    older = _record("task-a")
    older["created_at"] = 1.0
    newer = _record("task-b")
    newer["created_at"] = 2.0

    stored, created = await store.create(older, idempotency_key="same")
    replayed, replay_created = await store.create(newer, idempotency_key="same")
    await store.create(newer, idempotency_key="different")

    assert created is True and replay_created is False
    assert stored["task_id"] == replayed["task_id"] == "task-a"
    assert [record["task_id"] for record in await store.list_for_tenant("tenant-a", limit=10)] == [
        "task-b",
        "task-a",
    ]
    assert await store.list_for_tenant("tenant-b", limit=10) == []
    assert all("same" not in str(key) for key in await client.keys("*"))
    await store.aclose()


async def test_redis_store_conditionally_updates_status() -> None:
    client = FakeRedis(decode_responses=True)
    store = RedisTaskStore(cast(Redis, client), key_prefix="test")
    await store.create(_record("task-a", status="running"))

    await store.update("task-a", {"status": "cancelled"}, expected_statuses={"running"})
    rejected = await store.update("task-a", {"status": "completed"}, expected_statuses={"running"})

    assert rejected is not None and rejected["status"] == "cancelled"
    await store.aclose()


async def test_cancel_terminates_background_graph_and_stays_cancelled() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingGraph:
        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    app = create_app(Settings(database_url="memory://", rate_limit_rps=1000))
    principal = Principal("tenant-a", "user-1", frozenset({"admin"}))
    request = SimpleNamespace(app=app)
    async with app.router.lifespan_context(app):
        app.state.graph = BlockingGraph()
        created = await create_task(TaskCreateRequest(input="block"), request, principal, None)
        await asyncio.wait_for(started.wait(), timeout=1)

        response = await cancel_task(created.task_id, request, principal)
        await asyncio.wait_for(stopped.wait(), timeout=1)
        await asyncio.sleep(0)

        record = await app.state.task_store.get(created.task_id)
        assert response.status == "cancelled"
        assert record is not None and record["status"] == "cancelled"
        assert created.task_id not in app.state.task_runs
