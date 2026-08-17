from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.router import ModelRouter
from pk_agent.providers.contract import KnowledgeChunk, KnowledgeHit, KnowledgeIngestionJob
from pk_agent.skills.models import SkillRecord, TriggerCondition
from pk_agent.skills.registry import SkillRegistry
from pk_agent.skills.store import InMemorySkillStore


class _EmptyProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list[KnowledgeHit]:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list[KnowledgeChunk]:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return "empty-job"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> KnowledgeIngestionJob:
        return KnowledgeIngestionJob(job_id=job_id, status="completed")


class _CapturingModel:
    def __init__(self) -> None:
        self.prompts: dict[str, str] = {}

    async def generate(self, prompt: str, *, capability: str) -> str:
        self.prompts[capability] = prompt
        return json.dumps({"answer": "done", "claims": []})


async def _skill_registry() -> SkillRegistry:
    store = InMemorySkillStore()
    await store.put_record(
        SkillRecord(
            skill_id="kv-cache-compare",
            version="1.0.0",
            status="approved",
            name="Compare KV-cache compression",
            description="desc",
            trigger_conditions=[TriggerCondition(keywords=["kv cache"], priority=1)],
            procedure="kv-cache procedure steps",
            required_tools=["model_block_run"],
        )
    )
    registry = SkillRegistry(store)
    await registry.load_approved()
    return registry


def _graph(model: _CapturingModel, skill_registry: SkillRegistry | None) -> Any:
    router = ModelRouter()
    router.register("answer", model)
    return build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        3,
        knowledge_provider=_EmptyProvider(),
        skill_registry=skill_registry,
    )


def _state(task_id: str, query: str) -> dict:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "status": "pending",
        "input": query,
        "require_approval": False,
        "approved": False,
        "allow_external": False,
        "budget_calls": 0,
        "messages": [],
    }


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": f"tenant-a:{task_id}"}}


async def test_matched_skill_is_injected_into_answer_prompt() -> None:
    model = _CapturingModel()
    registry = await _skill_registry()
    graph = _graph(model, registry)

    result = await graph.ainvoke(
        _state("skill-1", "How does kv cache compression work?"),
        config=_config("skill-1"),
    )

    assert result["status"] == "completed"
    assert result["matched_skill_ids"] == ["kv-cache-compare"]
    answer_prompt = model.prompts["answer"]
    assert "Approved reusable procedures" in answer_prompt
    assert "kv-cache procedure steps" in answer_prompt


async def test_unmatched_query_injects_nothing() -> None:
    model = _CapturingModel()
    registry = await _skill_registry()
    graph = _graph(model, registry)

    result = await graph.ainvoke(
        _state("skill-2", "unrelated cooking question"),
        config=_config("skill-2"),
    )

    assert result["matched_skill_ids"] == []
    assert "Approved reusable procedures" not in model.prompts["answer"]


async def test_graph_without_skill_registry_still_completes() -> None:
    model = _CapturingModel()
    graph = _graph(model, None)

    result = await graph.ainvoke(
        _state("skill-3", "How does kv cache compression work?"),
        config=_config("skill-3"),
    )

    assert result["status"] == "completed"
    assert result["matched_skill_ids"] == []
    assert "Approved reusable procedures" not in model.prompts["answer"]
