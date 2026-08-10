from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver

from pk_agent.graph.checkpointer import TenantRlsCheckpointer


class Transaction(AbstractAsyncContextManager[None]):
    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[str, ...]]] = []

    def transaction(self) -> Transaction:
        return Transaction()

    async def execute(self, query: str, parameters: tuple[str, ...]) -> None:
        self.executions.append((query, parameters))


class FakeSaver:
    def __init__(self) -> None:
        self.conn = FakeConnection()
        self.serde = object()
        self.puts = 0

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        self.puts += 1
        return {
            "configurable": {
                "thread_id": config["configurable"]["thread_id"],
                "checkpoint_ns": "test",
                "checkpoint_id": "checkpoint-1",
            }
        }


def test_checkpointer_is_a_langgraph_saver() -> None:
    assert isinstance(TenantRlsCheckpointer(FakeSaver()), BaseCheckpointSaver)


async def test_checkpointer_sets_transaction_tenant_and_preserves_scope() -> None:
    saver = FakeSaver()
    scoped = TenantRlsCheckpointer(saver)
    config = {
        "configurable": {
            "tenant_id": "tenant-a",
            "thread_id": "tenant-a:task-a",
            "checkpoint_ns": "test",
        }
    }

    result = await scoped.aput(config, {}, {}, {})

    assert saver.puts == 1
    assert saver.conn.executions[0][1] == ("tenant-a",)
    assert result["configurable"]["tenant_id"] == "tenant-a"


async def test_checkpointer_rejects_cross_tenant_thread_before_database_access() -> None:
    saver = FakeSaver()
    scoped = TenantRlsCheckpointer(saver)
    config = {
        "configurable": {
            "tenant_id": "tenant-a",
            "thread_id": "tenant-b:task-a",
        }
    }

    with pytest.raises(ValueError, match="does not match"):
        await scoped.aput(config, {}, {}, {})
    assert saver.puts == 0
    assert saver.conn.executions == []
