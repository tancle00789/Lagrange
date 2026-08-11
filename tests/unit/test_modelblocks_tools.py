from __future__ import annotations

import pytest

from pk_agent.harness.registry import TransientToolError
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock
from pk_agent.modelblocks.models import ModelRunResult
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.modelblocks.tools import make_model_run_handler


def _registry() -> ModelBlockRegistry:
    registry = ModelBlockRegistry()
    registry.register(KvCacheSimBlock())
    return registry


class _StubBroker:
    def __init__(self, result: ModelRunResult | None = None, fail_submit: bool = False):
        self.result = result
        self.fail_submit = fail_submit
        self.submitted = []

    async def submit(self, request) -> str:
        if self.fail_submit:
            raise ConnectionError("redis down")
        self.submitted.append(request)
        return request.run_id

    async def await_result(self, run_id: str, *, timeout_seconds: float):
        return self.result


_OK_RESULT = ModelRunResult(
    run_id="r1",
    block_name="kv_cache_sim",
    status="completed",
    metrics={"baseline_memory_gib": 1.0},
    artifact_ids=["tenant-a/task-1/r1-kv_cache_curve.json"],
)


async def test_handler_success_roundtrip() -> None:
    broker = _StubBroker(result=_OK_RESULT)
    handler = make_model_run_handler(_registry(), broker, timeout_seconds=5)
    data = await handler(
        {
            "block_name": "kv_cache_sim",
            "params": {"num_layers": 8},
            "tenant_id": "tenant-a",
            "task_id": "task-1",
        }
    )
    assert data["status"] == "completed"
    assert data["metrics"]["baseline_memory_gib"] == 1.0
    assert broker.submitted[0].tenant_id == "tenant-a"
    assert broker.submitted[0].block_name == "kv_cache_sim"


async def test_handler_revalidates_schema_server_side() -> None:
    broker = _StubBroker(result=_OK_RESULT)
    handler = make_model_run_handler(_registry(), broker, timeout_seconds=5)
    with pytest.raises(ValueError, match="schema"):
        await handler(
            {
                "block_name": "kv_cache_sim",
                "params": {"num_layers": -5},
                "tenant_id": "tenant-a",
                "task_id": "task-1",
            }
        )
    assert broker.submitted == []


async def test_handler_rejects_unknown_block() -> None:
    handler = make_model_run_handler(_registry(), _StubBroker(), timeout_seconds=5)
    with pytest.raises(ValueError, match="unknown model block"):
        await handler(
            {
                "block_name": "does_not_exist",
                "params": {},
                "tenant_id": "tenant-a",
                "task_id": "task-1",
            }
        )


async def test_handler_rejects_malformed_payload() -> None:
    handler = make_model_run_handler(_registry(), _StubBroker(), timeout_seconds=5)
    with pytest.raises(ValueError, match="block_name"):
        await handler({"block_name": "", "params": {}, "tenant_id": "t", "task_id": "t"})
    with pytest.raises(ValueError, match="params"):
        await handler(
            {"block_name": "kv_cache_sim", "params": "nope", "tenant_id": "t", "task_id": "t"}
        )
    with pytest.raises(ValueError, match="tenant_id"):
        await handler(
            {"block_name": "kv_cache_sim", "params": {}, "tenant_id": "", "task_id": "t"}
        )


async def test_handler_submit_failure_is_transient() -> None:
    broker = _StubBroker(fail_submit=True)
    handler = make_model_run_handler(_registry(), broker, timeout_seconds=5)
    with pytest.raises(TransientToolError, match="submit failed"):
        await handler(
            {
                "block_name": "kv_cache_sim",
                "params": {},
                "tenant_id": "tenant-a",
                "task_id": "task-1",
            }
        )


async def test_handler_timeout_is_transient() -> None:
    broker = _StubBroker(result=None)
    handler = make_model_run_handler(_registry(), broker, timeout_seconds=0.1)
    with pytest.raises(TransientToolError, match="timed out"):
        await handler(
            {
                "block_name": "kv_cache_sim",
                "params": {},
                "tenant_id": "tenant-a",
                "task_id": "task-1",
            }
        )
