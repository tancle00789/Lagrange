from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import _parse_model_output, build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.router import FakeModel, ModelRouter
from pk_agent.providers.contract import KnowledgeChunk, KnowledgeHit, KnowledgeIngestionJob
from pk_agent.providers.fake import FakeKnowledgeProvider


class _EmptyProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list[KnowledgeChunk]:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return "empty-job"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="completed")


def _graph(provider: Any, model: FakeModel) -> Any:
    router = ModelRouter()
    router.register("answer", model)
    return build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        3,
        knowledge_provider=provider,
    )


def _state(task_id: str, *, require_approval: bool = False) -> dict:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "status": "pending",
        "input": "  what is   pk agent?  ",
        "require_approval": require_approval,
        "approved": False,
        "budget_calls": 0,
        "messages": [],
    }


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": f"tenant-a:{task_id}"}}


async def test_happy_path_grounded_report() -> None:
    response = json.dumps(
        {
            "answer": "PK Agent is a research agent.",
            "claims": [
                {"text": "PK Agent is a research agent.", "chunk_ids": ["fake-chunk-tenant-a-0"]}
            ],
        }
    )
    graph = _graph(FakeKnowledgeProvider(), FakeModel(response=response))
    result = await graph.ainvoke(_state("happy-1"), config=_config("happy-1"))

    assert result["status"] == "completed"
    assert result["normalized_query"] == "what is pk agent?"
    assert result["result"] == "PK Agent is a research agent."
    assert result["budget_calls"] == 1

    report = result["report"]
    assert report["grounded"] is True
    assert report["dropped_citations"] == 0
    assert report["claims"] == [
        {"text": "PK Agent is a research agent.", "chunk_ids": ["fake-chunk-tenant-a-0"]}
    ]


async def test_require_approval_pauses() -> None:
    graph = _graph(FakeKnowledgeProvider(), FakeModel())
    result = await graph.ainvoke(
        _state("pause-1", require_approval=True), config=_config("pause-1")
    )
    assert result["status"] == "paused"
    assert result["report"]["grounded"] is True


async def test_non_json_output_synthesizes_claim_from_top_hit() -> None:
    graph = _graph(FakeKnowledgeProvider(), FakeModel())
    result = await graph.ainvoke(_state("plain-1"), config=_config("plain-1"))

    report = result["report"]
    assert report["grounded"] is True
    assert len(report["claims"]) == 1
    assert report["claims"][0]["chunk_ids"] == ["fake-chunk-tenant-a-0"]
    assert any("synthesized" in note for note in report["notes"])
    assert result["result"].startswith("FAKE[answer]:")


async def test_invalid_citations_dropped_and_counted() -> None:
    response = json.dumps(
        {
            "answer": "mixed citations",
            "claims": [
                {
                    "text": "partially valid",
                    "chunk_ids": ["fake-chunk-tenant-a-0", "bogus-1"],
                },
                {"text": "fully bogus", "chunk_ids": ["bogus-2"]},
            ],
        }
    )
    graph = _graph(FakeKnowledgeProvider(), FakeModel(response=response))
    result = await graph.ainvoke(_state("drop-1"), config=_config("drop-1"))

    report = result["report"]
    assert report["dropped_citations"] == 2
    assert report["claims"] == [
        {"text": "partially valid", "chunk_ids": ["fake-chunk-tenant-a-0"]}
    ]
    assert report["grounded"] is True


async def test_no_hits_marks_report_ungrounded() -> None:
    response = json.dumps(
        {
            "answer": "no evidence available",
            "claims": [{"text": "unsupported", "chunk_ids": ["fake-chunk-tenant-a-0"]}],
        }
    )
    graph = _graph(_EmptyProvider(), FakeModel(response=response))
    result = await graph.ainvoke(_state("empty-1"), config=_config("empty-1"))

    report = result["report"]
    assert result["knowledge_hits"] == []
    assert report["grounded"] is False
    assert report["claims"] == []
    assert any("ungrounded" in note for note in report["notes"])


def test_parse_model_output_strips_code_fence() -> None:
    raw = '```json\n{"answer": "a", "claims": [{"text": "c", "chunk_ids": ["x"]}]}\n```'
    answer, claims = _parse_model_output(raw)
    assert answer == "a"
    assert claims[0].text == "c"
    assert claims[0].chunk_ids == ["x"]


def test_parse_model_output_falls_back_to_plain_text() -> None:
    answer, claims = _parse_model_output("not json at all")
    assert answer == "not json at all"
    assert claims == []


def test_parse_model_output_truncates_claim_text() -> None:
    raw = json.dumps({"answer": "a", "claims": [{"text": "x" * 800, "chunk_ids": ["c1"]}]})
    _, claims = _parse_model_output(raw)
    assert len(claims[0].text) == 600
