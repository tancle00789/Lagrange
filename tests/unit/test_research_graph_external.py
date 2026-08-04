from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import _dedupe_external, build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.registry import ToolRegistry, ToolRisk, ToolSpec
from pk_agent.harness.router import FakeModel, ModelRouter
from pk_agent.providers.contract import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeIngestionJob,
    SourceReference,
)

EXTERNAL_RECORD = {
    "source_type": "arxiv",
    "external_id": "2401.00001",
    "title": "KV Cache Compression",
    "authors": ["A. Researcher"],
    "abstract": "Compressing KV caches for long-context LLMs.",
    "url": "https://arxiv.org/abs/2401.00001",
    "pdf_url": None,
    "doi": None,
    "venue": "",
    "published": "2024-01-01",
    "extra": {},
}


class _EmptyProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list[KnowledgeChunk]:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return "empty-job"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="completed")


class _ThreeHitProvider(_EmptyProvider):
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        return [
            KnowledgeHit(
                chunk_id=f"chunk-{i}",
                score=0.9 - i * 0.1,
                source=SourceReference(kb_id="kb", document_id=f"doc-{i}"),
                snippet=f"internal evidence {i}",
            )
            for i in range(3)
        ]


def _registry(handler: Any = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="literature_search",
            version="1.0",
            description="test",
            risk=ToolRisk.L1,
            handler=handler
            or (lambda payload: {"records": [EXTERNAL_RECORD], "errors": []}),
        )
    )
    return registry


def _graph(
    provider: Any,
    model: FakeModel,
    tool_registry: ToolRegistry | None = None,
) -> Any:
    router = ModelRouter()
    router.register("answer", model)
    return build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        3,
        knowledge_provider=provider,
        tool_registry=tool_registry,
    )


def _state(task_id: str, *, allow_external: bool) -> dict:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "status": "pending",
        "input": "kv cache compression?",
        "require_approval": False,
        "approved": False,
        "allow_external": allow_external,
        "ingest_external": False,
        "budget_calls": 0,
        "messages": [],
    }


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": f"tenant-a:{task_id}"}}


async def test_external_skipped_when_flag_off() -> None:
    graph = _graph(_EmptyProvider(), FakeModel(), _registry())
    result = await graph.ainvoke(
        _state("ext-off", allow_external=False), config=_config("ext-off")
    )
    assert result["status"] == "completed"
    assert result.get("external_sources", []) == []
    assert result.get("retrieval_rounds", 0) == 0


async def test_external_skipped_when_internal_hits_sufficient() -> None:
    graph = _graph(_ThreeHitProvider(), FakeModel(), _registry())
    result = await graph.ainvoke(
        _state("ext-skip", allow_external=True), config=_config("ext-skip")
    )
    assert result["knowledge_hits"]
    assert result.get("external_sources", []) == []
    assert result.get("retrieval_rounds", 0) == 0


async def test_external_retrieved_and_source_id_citable() -> None:
    response = json.dumps(
        {
            "answer": "KV cache compression reduces memory.",
            "claims": [
                {
                    "text": "KV cache compression reduces memory.",
                    "chunk_ids": ["arxiv:2401.00001", "bogus"],
                }
            ],
        }
    )
    graph = _graph(_EmptyProvider(), FakeModel(response=response), _registry())
    result = await graph.ainvoke(
        _state("ext-on", allow_external=True), config=_config("ext-on")
    )

    assert result["retrieval_rounds"] == 1
    sources = result["external_sources"]
    assert len(sources) == 1
    assert sources[0]["external_id"] == "2401.00001"

    report = result["report"]
    assert report["grounded"] is True
    assert report["dropped_citations"] == 1
    assert report["claims"] == [
        {
            "text": "KV cache compression reduces memory.",
            "chunk_ids": ["arxiv:2401.00001"],
        }
    ]


async def test_external_failure_degrades_and_completes() -> None:
    async def failing(payload: dict[str, Any]) -> None:
        raise RuntimeError("all sources down")

    graph = _graph(_EmptyProvider(), FakeModel(), _registry(failing))
    result = await graph.ainvoke(
        _state("ext-fail", allow_external=True), config=_config("ext-fail")
    )
    assert result["status"] == "completed"
    assert result["external_sources"] == []
    assert "all sources down" in result["external_error"]
    assert result["report"]["grounded"] is False


async def test_external_synthesizes_claim_when_model_gives_none() -> None:
    graph = _graph(_EmptyProvider(), FakeModel(), _registry())
    result = await graph.ainvoke(
        _state("ext-synth", allow_external=True), config=_config("ext-synth")
    )
    report = result["report"]
    assert report["grounded"] is True
    assert report["claims"][0]["chunk_ids"] == ["arxiv:2401.00001"]
    assert any("synthesized" in note for note in report["notes"])


def test_dedupe_external_by_doi_url_and_cap() -> None:
    a = {**EXTERNAL_RECORD, "external_id": "1"}
    dupe_url = {
        **EXTERNAL_RECORD,
        "url": "https://arxiv.org/abs/2401.00001/",
        "external_id": "2",
    }
    b = {
        **EXTERNAL_RECORD,
        "doi": "10.1/X",
        "url": "https://arxiv.org/abs/2401.00003",
        "external_id": "3",
    }
    dupe_doi = {
        **EXTERNAL_RECORD,
        "doi": "10.1/x",
        "url": "https://elsewhere.dev/paper",
        "external_id": "4",
    }
    unique = {
        **EXTERNAL_RECORD,
        "url": "https://arxiv.org/abs/2401.00002",
        "external_id": "5",
    }
    merged = _dedupe_external([a, dupe_url, b, dupe_doi, unique])
    assert [r["external_id"] for r in merged] == ["1", "3", "5"]


def test_dedupe_external_caps_at_limit() -> None:
    records = [
        {**EXTERNAL_RECORD, "external_id": f"id-{i}", "url": f"https://x.dev/{i}"}
        for i in range(20)
    ]
    assert len(_dedupe_external(records)) == 8
