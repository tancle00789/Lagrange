from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from pk_agent.agentos.api.v1.tasks import execute_job
from pk_agent.agentos.job_queue import JobEnvelope, RetryableJobError
from pk_agent.agentos.task_store import InMemoryTaskStore
from pk_agent.config import Settings


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        self.events.append((task_id, event))


def _job() -> JobEnvelope:
    return JobEnvelope(
        task_id="task-a",
        initial_state={"task_id": "task-a", "tenant_id": "tenant-a", "input": "research"},
        config={
            "configurable": {
                "thread_id": "tenant-a:task-a",
                "tenant_id": "tenant-a",
            }
        },
    )


async def _app(graph: Any, *, max_attempts: int = 3) -> Any:
    store = InMemoryTaskStore()
    await store.create(
        {
            "task_id": "task-a",
            "tenant_id": "tenant-a",
            "thread_id": "tenant-a:task-a",
            "status": "pending",
            "attempts": 0,
            "created_at": 0,
        }
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            task_store=store,
            graph=graph,
            event_bus=RecordingBus(),
            settings=Settings(worker_max_attempts=max_attempts),
            task_runs={},
            shutting_down=False,
        )
    )


async def test_execute_job_stops_after_configured_attempts() -> None:
    class FailingGraph:
        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> None:
            raise RuntimeError("provider unavailable")

    app = await _app(FailingGraph())

    with pytest.raises(RetryableJobError):
        await execute_job(app, _job())
    with pytest.raises(RetryableJobError):
        await execute_job(app, _job())
    await execute_job(app, _job())

    record = await app.state.task_store.get("task-a")
    assert record is not None
    assert record["attempts"] == 3
    assert record["status"] == "failed"
    assert [event[1]["type"] for event in app.state.event_bus.events] == [
        "retrying",
        "retrying",
        "failed",
    ]


async def test_shutdown_marks_inflight_job_retryable_for_takeover() -> None:
    started = asyncio.Event()

    class BlockingGraph:
        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> None:
            started.set()
            await asyncio.Event().wait()

    app = await _app(BlockingGraph())
    delivery = asyncio.create_task(execute_job(app, _job()))
    await asyncio.wait_for(started.wait(), timeout=1)
    app.state.shutting_down = True
    delivery.cancel()

    with pytest.raises(asyncio.CancelledError):
        await delivery
    record = await app.state.task_store.get("task-a")
    assert record is not None
    assert record["status"] == "retrying"
    assert record["attempts"] == 1


async def test_execute_job_rejects_tampered_tenant_envelope_without_running_graph() -> None:
    class UnexpectedGraph:
        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> None:
            raise AssertionError("tampered job must not reach graph")

    app = await _app(UnexpectedGraph())
    job = _job()
    job.initial_state["tenant_id"] = "tenant-b"

    await execute_job(app, job)

    record = await app.state.task_store.get("task-a")
    assert record is not None and record["status"] == "pending"
    assert app.state.event_bus.events == [
        ("task-a", {"type": "security_rejected", "reason": "job tenant boundary mismatch"})
    ]
