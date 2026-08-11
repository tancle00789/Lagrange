from __future__ import annotations

import asyncio

from fakeredis.aioredis import FakeRedis

from pk_agent.modelblocks.artifacts import LocalArtifactStore
from pk_agent.modelblocks.broker import ModelRunBroker, run_model_worker
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock
from pk_agent.modelblocks.models import ModelRunRequest
from pk_agent.modelblocks.registry import ModelBlockRegistry


def _registry() -> ModelBlockRegistry:
    registry = ModelBlockRegistry()
    registry.register(KvCacheSimBlock())
    return registry


async def test_broker_worker_roundtrip(tmp_path) -> None:
    client = FakeRedis(decode_responses=True)
    broker = ModelRunBroker(client, key_prefix="test")
    stop = asyncio.Event()
    worker = asyncio.create_task(
        run_model_worker(
            _registry(),
            LocalArtifactStore(tmp_path),
            client,
            key_prefix="test",
            block_ms=50,
            stop_event=stop,
        )
    )
    try:
        request = ModelRunRequest(
            run_id="run-broker-1",
            tenant_id="tenant-a",
            task_id="task-1",
            block_name="kv_cache_sim",
            params={"num_layers": 4, "context_length": 512},
        )
        run_id = await broker.submit(request)
        result = await broker.await_result(run_id, timeout_seconds=10.0)
        assert result is not None
        assert result.status == "completed"
        assert result.block_name == "kv_cache_sim"
        assert result.metrics["baseline_memory_gib"] > 0
        assert result.artifact_ids[0].startswith("tenant-a/task-1/")
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=5.0)


async def test_broker_worker_invalid_block_fails_run(tmp_path) -> None:
    client = FakeRedis(decode_responses=True)
    broker = ModelRunBroker(client, key_prefix="test2")
    stop = asyncio.Event()
    worker = asyncio.create_task(
        run_model_worker(
            _registry(),
            LocalArtifactStore(tmp_path),
            client,
            key_prefix="test2",
            block_ms=50,
            stop_event=stop,
        )
    )
    try:
        request = ModelRunRequest(
            run_id="run-broker-2",
            tenant_id="tenant-a",
            task_id="task-1",
            block_name="not_registered",
            params={},
        )
        await broker.submit(request)
        result = await broker.await_result("run-broker-2", timeout_seconds=10.0)
        assert result is not None
        assert result.status == "failed"
        assert result.error and "unknown model block" in result.error
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=5.0)


async def test_await_result_times_out() -> None:
    client = FakeRedis(decode_responses=True)
    broker = ModelRunBroker(client, key_prefix="test3")
    result = await broker.await_result("never-coming", timeout_seconds=0.2)
    assert result is None
