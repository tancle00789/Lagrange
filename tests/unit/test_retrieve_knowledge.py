from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.router import FakeModel, ModelRouter
from pk_agent.providers.contract import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeIngestionJob,
)
from pk_agent.providers.errors import KnowledgeAuthError, KnowledgeUnavailable


class _UnavailableProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        raise KnowledgeUnavailable("weknora down")

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list[KnowledgeChunk]:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        raise KnowledgeUnavailable("weknora down")

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="unknown")


class _AuthErrorProvider(_UnavailableProvider):
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        raise KnowledgeAuthError("bad api key")


def _graph(provider: Any) -> Any:
    router = ModelRouter()
    router.register("answer", FakeModel())
    return build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        3,
        knowledge_provider=provider,
    )


def _state(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "status": "pending",
        "input": "hello",
        "require_approval": False,
        "approved": False,
        "budget_calls": 0,
        "messages": [],
    }


async def test_retrieval_outage_degrades_to_empty_hits() -> None:
    graph = _graph(_UnavailableProvider())
    result = await graph.ainvoke(
        _state("deg-1"), config={"configurable": {"thread_id": "tenant-a:deg-1"}}
    )
    assert result["status"] == "completed"
    assert result["knowledge_hits"] == []
    assert "weknora down" in result["knowledge_error"]
    assert result["result"].startswith("FAKE[answer]:")
    assert result["report"]["grounded"] is False


async def test_auth_error_fails_task_loudly() -> None:
    graph = _graph(_AuthErrorProvider())
    with pytest.raises(KnowledgeAuthError):
        await graph.ainvoke(
            _state("deg-2"), config={"configurable": {"thread_id": "tenant-a:deg-2"}}
        )
