from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import _parse_model_plan, build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.registry import ToolRegistry, ToolRisk, ToolSpec
from pk_agent.harness.router import ModelRouter
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.modelblocks.tools import MODEL_BLOCK_RUN_TOOL


class _ScriptedModel:
    """Returns a plan JSON for capability 'plan', an answer JSON for 'answer'."""

    def __init__(self, plan: str, answer: str | None = None) -> None:
        self.plan = plan
        self.answer = answer or json.dumps({"answer": "done", "claims": []})
        self.plan_prompts: list[str] = []
        self.answer_prompts: list[str] = []

    async def generate(self, prompt: str, *, capability: str) -> str:
        if capability == "plan":
            self.plan_prompts.append(prompt)
            return self.plan
        self.answer_prompts.append(prompt)
        return self.answer


class _EmptyProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return "job"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str):
        from pk_agent.providers.contract import KnowledgeIngestionJob

        return KnowledgeIngestionJob(job_id=job_id, status="completed")


def _model_block_registry() -> ModelBlockRegistry:
    registry = ModelBlockRegistry()
    registry.register(KvCacheSimBlock())
    return registry


def _tool_registry(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=MODEL_BLOCK_RUN_TOOL,
            version="1.0",
            description="test",
            risk=ToolRisk.L2,
            handler=handler,
        )
    )
    return registry


def _graph(model: Any, tool_registry: ToolRegistry | None, max_calls: int = 2) -> Any:
    router = ModelRouter()
    router.register("answer", model)
    router.register("plan", model)
    return build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        10,
        knowledge_provider=_EmptyProvider(),
        tool_registry=tool_registry,
        model_block_registry=_model_block_registry(),
        model_block_max_calls=max_calls,
    )


def _state(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "tenant_id": "tenant-a",
        "status": "pending",
        "input": "simulate kv cache memory for a 32-layer model",
        "require_approval": False,
        "approved": False,
        "allow_external": False,
        "budget_calls": 0,
        "messages": [],
    }


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": f"tenant-a:{task_id}"}}


_PLAN_ONE = json.dumps(
    {"model_calls": [{"block_name": "kv_cache_sim", "params": {"num_layers": 8}}]}
)


async def test_plan_and_run_happy_path() -> None:
    async def run_handler(payload: dict[str, Any]) -> dict:
        assert payload["block_name"] == "kv_cache_sim"
        assert payload["tenant_id"] == "tenant-a"
        return {
            "run_id": "r-1",
            "block_name": "kv_cache_sim",
            "status": "completed",
            "metrics": {"baseline_memory_gib": 0.5},
            "artifact_ids": ["tenant-a/t/r-1-curve.json"],
            "error": None,
        }

    model = _ScriptedModel(plan=_PLAN_ONE)
    graph = _graph(model, _tool_registry(run_handler))
    result = await graph.ainvoke(_state("mb-happy"), config=_config("mb-happy"))

    assert result["status"] == "completed"
    runs = result["model_runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["metrics"]["baseline_memory_gib"] == 0.5
    # The simulation section must reach the answer prompt.
    assert any("Simulation results" in prompt for prompt in model.answer_prompts)
    # Budget: 1 (plan) + 1 (run) + 1 (answer)
    assert result["budget_calls"] == 3


async def test_plan_rejects_unknown_block_and_completes() -> None:
    plan = json.dumps({"model_calls": [{"block_name": "evil_block", "params": {}}]})
    called = []

    async def run_handler(payload: dict[str, Any]) -> dict:
        called.append(payload)
        return {}

    model = _ScriptedModel(plan=plan)
    graph = _graph(model, _tool_registry(run_handler))
    result = await graph.ainvoke(_state("mb-bad"), config=_config("mb-bad"))

    assert result["status"] == "completed"
    assert result["model_calls"] == []
    assert result["model_runs"] == []
    assert "evil_block" in result["model_error"]
    assert called == []


async def test_plan_rejects_bad_params() -> None:
    plan = json.dumps(
        {"model_calls": [{"block_name": "kv_cache_sim", "params": {"num_layers": -1}}]}
    )
    model = _ScriptedModel(plan=plan)
    graph = _graph(model, _tool_registry(lambda payload: {}))
    result = await graph.ainvoke(_state("mb-badparams"), config=_config("mb-badparams"))
    assert result["model_calls"] == []
    assert "schema" in result["model_error"]


async def test_plan_capped_at_max_calls() -> None:
    plan = json.dumps(
        {
            "model_calls": [
                {"block_name": "kv_cache_sim", "params": {}},
                {"block_name": "kv_cache_sim", "params": {"num_layers": 8}},
                {"block_name": "kv_cache_sim", "params": {"num_layers": 16}},
            ]
        }
    )
    calls: list[dict] = []

    async def run_handler(payload: dict[str, Any]) -> dict:
        calls.append(payload)
        return {
            "run_id": f"r-{len(calls)}",
            "status": "completed",
            "metrics": {},
            "artifact_ids": [],
            "error": None,
        }

    model = _ScriptedModel(plan=plan)
    graph = _graph(model, _tool_registry(run_handler), max_calls=2)
    result = await graph.ainvoke(_state("mb-cap"), config=_config("mb-cap"))
    assert len(result["model_calls"]) == 2
    assert len(result["model_runs"]) == 2
    assert "over max calls" in result["model_error"]


async def test_plan_unparseable_degrades_to_no_calls() -> None:
    model = _ScriptedModel(plan="not json at all")
    graph = _graph(model, _tool_registry(lambda payload: {}))
    result = await graph.ainvoke(_state("mb-garbage"), config=_config("mb-garbage"))
    assert result["status"] == "completed"
    assert result["model_calls"] == []


async def test_run_failure_degrades_and_completes() -> None:
    async def failing(payload: dict[str, Any]) -> None:
        raise RuntimeError("executor down")

    model = _ScriptedModel(plan=_PLAN_ONE)
    graph = _graph(model, _tool_registry(failing))
    result = await graph.ainvoke(_state("mb-runfail"), config=_config("mb-runfail"))

    assert result["status"] == "completed"
    assert result["model_runs"][0]["status"] == "failed"
    assert "executor down" in result["model_error"]
    assert not any("Simulation results" in prompt for prompt in model.answer_prompts)


async def test_no_plan_capability_skips_planning() -> None:
    router_only_answer = _ScriptedModel(plan=_PLAN_ONE)
    router = ModelRouter()
    router.register("answer", router_only_answer)
    graph = build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        10,
        knowledge_provider=_EmptyProvider(),
        tool_registry=_tool_registry(lambda payload: {}),
        model_block_registry=_model_block_registry(),
        model_block_max_calls=2,
    )
    result = await graph.ainvoke(_state("mb-noplan"), config=_config("mb-noplan"))
    assert result["status"] == "completed"
    assert result["model_calls"] == []


async def test_no_model_block_registry_skips_planning() -> None:
    router = ModelRouter()
    model = _ScriptedModel(plan=_PLAN_ONE)
    router.register("answer", model)
    router.register("plan", model)
    graph = build_research_graph(
        InMemorySaver(),
        router,
        AgentEventBus(),
        10,
        knowledge_provider=_EmptyProvider(),
        tool_registry=_tool_registry(lambda payload: {}),
        model_block_registry=None,
    )
    result = await graph.ainvoke(_state("mb-noreg"), config=_config("mb-noreg"))
    assert result["status"] == "completed"
    assert result.get("model_calls", []) == []
    assert model.plan_prompts == []


def test_parse_model_plan_defensive() -> None:
    assert _parse_model_plan("garbage") == []
    assert _parse_model_plan('{"model_calls": "nope"}') == []
    assert _parse_model_plan('[{"block_name": "x"}]') == []
    assert _parse_model_plan(
        '{"model_calls": [{"block_name": "kv_cache_sim", "params": "bad"}]}'
    ) == []
    fenced = "```json\n" + _PLAN_ONE + "\n```"
    parsed = _parse_model_plan(fenced)
    assert parsed == [{"block_name": "kv_cache_sim", "params": {"num_layers": 8}}]
