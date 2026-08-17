from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pk_agent.graph.research_graph import build_research_graph
from pk_agent.harness.bus import AgentEventBus
from pk_agent.harness.registry import ToolRegistry, ToolRisk, ToolSpec
from pk_agent.harness.router import ModelRouter
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.modelblocks.tools import MODEL_BLOCK_RUN_TOOL
from pk_agent.skills.models import EvalCase, EvalResult, SkillRecord
from pk_agent.skills.registry import SkillRegistry
from pk_agent.skills.store import InMemorySkillStore


class _ReplayModel:
    """Deterministic model for offline replay — never a real model.

    - capability "answer": echoes the injected trusted skills section (this
      verifies the injection → answer plumbing end to end).
    - capability "plan": returns model_calls for the blocks the case expects,
      with empty params (block schemas all have defaults).
    """

    def __init__(self) -> None:
        # Filled per-case by the evaluator before graph invocation.
        self._expected_blocks: list[str] = []

    async def generate(self, prompt: str, *, capability: str) -> str:
        if capability == "plan":
            calls = [{"block_name": name, "params": {}} for name in self._expected_blocks]
            return json.dumps({"model_calls": calls})
        marker = "Approved reusable procedures"
        start = prompt.find(marker)
        skills_section = prompt[start : start + 2000] if start != -1 else ""
        answer = f"replay answer. {skills_section}"[:3000]
        return json.dumps({"answer": answer, "claims": []})


class _EmptyProvider:
    async def search(self, query: str, *, tenant_id: str, limit: int = 10) -> list:
        return []

    async def get_chunks(self, chunk_ids: list[str], *, tenant_id: str) -> list:
        return []

    async def ingest(self, document: dict[str, Any], *, tenant_id: str) -> str:
        return "job"

    async def get_ingestion_status(self, job_id: str, *, tenant_id: str) -> Any:
        from pk_agent.providers.contract import KnowledgeIngestionJob

        return KnowledgeIngestionJob(job_id=job_id, status="completed")


def _stub_tool_registry() -> ToolRegistry:
    async def run_handler(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": f"eval-{uuid.uuid4().hex[:8]}",
            "block_name": payload["block_name"],
            "status": "completed",
            "metrics": {"eval": 1.0},
            "artifact_ids": [],
            "error": None,
        }

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=MODEL_BLOCK_RUN_TOOL,
            version="1.0",
            description="eval stub",
            risk=ToolRisk.L2,
            handler=run_handler,
        )
    )
    return registry


class SkillEvaluator:
    """Offline, deterministic evaluation of a candidate skill.

    Replays each eval case through the real research graph with a scripted
    model (no real LLM). The pass rate gates approval; an optional LLM judge
    (capability "judge") may add notes in production but never changes the
    deterministic pass/fail outcome.
    """

    def __init__(
        self,
        *,
        model_block_registry: ModelBlockRegistry | None = None,
        max_budget_calls: int = 10,
        judge_router: ModelRouter | None = None,
    ) -> None:
        self._model_block_registry = model_block_registry
        self._max_budget_calls = max_budget_calls
        self._judge_router = judge_router

    async def evaluate(
        self,
        record: SkillRecord,
        cases: list[EvalCase],
        *,
        baseline: SkillRecord | None = None,
    ) -> EvalResult:
        details: list[dict[str, Any]] = []
        passed = 0
        for case in cases:
            detail = await self._run_case(record, case)
            details.append(detail)
            if detail["passed"]:
                passed += 1

        total = len(cases)
        pass_rate = (passed / total) if total else 0.0

        regression = False
        compared_to: str | None = None
        if baseline is not None and baseline.eval_pass_rate > 0:
            compared_to = baseline.version
            regression = pass_rate < baseline.eval_pass_rate

        judge_notes: list[str] = []
        if self._judge_router is not None and cases:
            judge_notes = await self._judge(record, details)

        return EvalResult(
            run_id=uuid.uuid4().hex,
            total_cases=total,
            passed_cases=passed,
            eval_pass_rate=pass_rate,
            details=details,
            compared_to_version=compared_to,
            regression_detected=regression,
            judge_notes=judge_notes,
        )

    async def _run_case(
        self, record: SkillRecord, case: EvalCase
    ) -> dict[str, Any]:
        failures: list[str] = []

        # Evaluate the candidate as if it were approved: matching must work
        # on its trigger conditions.
        store = InMemorySkillStore()
        await store.put_record(record.model_copy(update={"status": "approved"}))
        skill_registry = SkillRegistry(store)
        await skill_registry.load_approved()

        model = _ReplayModel()
        model._expected_blocks = list(case.expect_model_blocks)
        router = ModelRouter()
        router.register("answer", model)
        router.register("plan", model)

        graph = build_research_graph(
            InMemorySaver(),
            router,
            AgentEventBus(),
            self._max_budget_calls,
            knowledge_provider=_EmptyProvider(),
            tool_registry=_stub_tool_registry(),
            model_block_registry=self._model_block_registry,
            skill_registry=skill_registry,
        )
        task_id = f"eval-{uuid.uuid4().hex[:12]}"
        final = await graph.ainvoke(
            {
                "task_id": task_id,
                "tenant_id": "tenant-eval",
                "status": "pending",
                "input": case.query,
                "require_approval": False,
                "approved": False,
                "allow_external": False,
                "budget_calls": 0,
                "messages": [],
            },
            config={"configurable": {"thread_id": f"tenant-eval:{task_id}"}},
        )

        matched = final.get("matched_skill_ids", [])
        if case.expect_skill_matched and case.expect_skill_matched not in matched:
            failures.append(
                f"skill {case.expect_skill_matched} not matched (got {matched})"
            )

        answer = str(final.get("result", "")).lower()
        for needle in case.expected_answer_contains:
            if needle.lower() not in answer:
                failures.append(f"answer missing expected substring: {needle!r}")

        ran_blocks = {
            str(run.get("block_name"))
            for run in final.get("model_runs", [])
            if run.get("status") == "completed"
        }
        for block in case.expect_model_blocks:
            if block not in ran_blocks:
                failures.append(f"model block {block} did not complete (got {sorted(ran_blocks)})")

        return {
            "case_id": case.case_id,
            "passed": not failures,
            "failures": failures,
            "matched_skill_ids": matched,
            "model_runs": [
                {"block_name": r.get("block_name"), "status": r.get("status")}
                for r in final.get("model_runs", [])
            ],
        }

    async def _judge(
        self, record: SkillRecord, details: list[dict[str, Any]]
    ) -> list[str]:
        """Optional LLM judge: notes only, never changes pass/fail."""
        assert self._judge_router is not None
        prompt = (
            "You are reviewing offline evaluation results for a candidate skill. "
            "Give at most 3 short bullet notes about risks or improvements. "
            "Plain text, no JSON.\n\n"
            f"Skill: {record.skill_id} v{record.version}\n"
            f"Procedure:\n{record.procedure[:1500]}\n\n"
            f"Case results:\n{json.dumps(details, ensure_ascii=False)[:3000]}"
        )
        try:
            raw = await self._judge_router.generate(prompt, capability="judge")
        except KeyError:
            return []
        notes = [
            line.strip("- •").strip()
            for line in raw.splitlines()
            if line.strip().startswith(("-", "•"))
        ]
        return notes[:3]
