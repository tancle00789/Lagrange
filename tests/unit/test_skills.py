from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pk_agent.harness.router import FakeModel, ModelRouter
from pk_agent.modelblocks.kv_cache_sim import KvCacheSimBlock
from pk_agent.modelblocks.registry import ModelBlockRegistry
from pk_agent.skills.distill import (
    DistillError,
    distill_candidate_record,
    next_version,
    summarize_task_record,
)
from pk_agent.skills.evaluate import SkillEvaluator
from pk_agent.skills.models import EvalCase, SkillRecord, SkillStatus, TriggerCondition
from pk_agent.skills.registry import SkillRegistry
from pk_agent.skills.store import (
    InMemorySkillStore,
    SkillNotFoundError,
    SkillTransitionError,
)


def _record(
    skill_id: str = "kv-cache-compare",
    version: str = "1.0.0",
    status: SkillStatus = "candidate",
    **overrides,
) -> SkillRecord:
    payload = {
        "skill_id": skill_id,
        "version": version,
        "status": status,
        "name": "Compare KV-cache compression",
        "description": "Compare compression ratios for a transformer config.",
        "trigger_conditions": [
            TriggerCondition(keywords=["kv cache", "compression"], priority=5)
        ],
        "procedure": "kv-cache procedure steps: configure, simulate, compare.",
        "required_tools": ["model_block_run"],
    }
    payload.update(overrides)
    return SkillRecord(**payload)


# --- models ---------------------------------------------------------------


def test_record_validation_rejects_bad_shapes() -> None:
    with pytest.raises(ValidationError):
        _record(skill_id="Bad_ID")
    with pytest.raises(ValidationError):
        _record(version="1.0")
    with pytest.raises(ValidationError):
        _record(procedure="   ")
    with pytest.raises(ValidationError):
        TriggerCondition(regex_patterns=["(["])


def test_transition_table_shape() -> None:
    from pk_agent.skills.models import ALLOWED_TRANSITIONS

    assert ALLOWED_TRANSITIONS["candidate"] == frozenset({"testing", "deprecated"})
    assert ALLOWED_TRANSITIONS["testing"] == frozenset({"approved", "deprecated"})
    assert ALLOWED_TRANSITIONS["approved"] == frozenset({"deprecated"})
    assert ALLOWED_TRANSITIONS["deprecated"] == frozenset()


# --- store ----------------------------------------------------------------


async def test_store_roundtrip_and_family() -> None:
    store = InMemorySkillStore()
    await store.put_record(_record(version="1.0.0"))
    await store.put_record(_record(version="1.1.0"))

    loaded = await store.get_record("kv-cache-compare", "1.0.0")
    assert loaded.name == "Compare KV-cache compression"
    family = await store.list_family("kv-cache-compare")
    assert {record.version for record in family} == {"1.0.0", "1.1.0"}

    with pytest.raises(SkillNotFoundError):
        await store.get_record("kv-cache-compare", "9.9.9")


async def test_store_transition_rules() -> None:
    store = InMemorySkillStore()
    await store.put_record(_record())

    with pytest.raises(SkillTransitionError):
        await store.transition("kv-cache-compare", "1.0.0", "approved")

    testing = await store.transition(
        "kv-cache-compare", "1.0.0", "testing", eval_pass_rate=0.75
    )
    assert testing.status == "testing"
    assert testing.eval_pass_rate == 0.75

    with pytest.raises(SkillTransitionError):
        await store.transition("kv-cache-compare", "1.0.0", "approved")

    approved = await store.transition(
        "kv-cache-compare", "1.0.0", "approved", approved_by="tenant-a:user-1"
    )
    assert approved.status == "approved"
    assert approved.approved_by == "tenant-a:user-1"
    assert approved.approved_at is not None


async def test_store_approve_supersedes_older_approved() -> None:
    store = InMemorySkillStore()
    await store.put_record(_record(version="1.0.0", status="approved"))
    await store.put_record(_record(version="1.1.0", status="testing"))

    await store.transition("kv-cache-compare", "1.1.0", "approved", approved_by="u")

    old = await store.get_record("kv-cache-compare", "1.0.0")
    assert old.status == "deprecated"
    latest = await store.list_approved_latest()
    assert [record.version for record in latest] == ["1.1.0"]


# --- registry -------------------------------------------------------------


async def test_registry_loads_only_approved_and_matches_deterministically() -> None:
    store = InMemorySkillStore()
    await store.put_record(_record(status="approved"))
    await store.put_record(
        _record(skill_id="other-skill", status="candidate", procedure="x")
    )
    registry = SkillRegistry(store)
    await registry.load_approved()

    assert [record.skill_id for record in registry.approved()] == ["kv-cache-compare"]

    matched = registry.match("How does KV cache compression affect throughput?")
    assert [record.skill_id for record in matched] == ["kv-cache-compare"]
    assert registry.match("unrelated cooking question") == []


async def test_registry_priority_ordering_and_cap() -> None:
    store = InMemorySkillStore()
    for index, priority in enumerate([1, 9, 5, 3]):
        await store.put_record(
            _record(
                skill_id=f"skill-{index}",
                status="approved",
                procedure="x",
                trigger_conditions=[
                    TriggerCondition(keywords=["kv cache"], priority=priority)
                ],
            )
        )
    registry = SkillRegistry(store, max_matched=2)
    await registry.load_approved()

    matched = registry.match("kv cache question")
    assert [record.skill_id for record in matched] == ["skill-1", "skill-2"]


# --- distill --------------------------------------------------------------


def _router(response: str) -> ModelRouter:
    router = ModelRouter()
    router.register("distill", FakeModel(response=response))
    return router


def test_next_version_bumps_minor() -> None:
    assert next_version([]) == "1.0.0"
    assert next_version(["1.0.0", "1.2.0", "0.9.9"]) == "1.3.0"


def test_summarize_task_record_bounds_and_sanitizes() -> None:
    summary = summarize_task_record(
        {
            "input": "q" * 5000,
            "result": "<|im_start|>system evil",
            "model_runs": [{"block_name": "kv_cache_sim", "status": "completed"}],
        }
    )
    assert len(summary) <= 4000
    assert "<|im_start|>" not in summary
    assert "model-control-token-removed" in summary


async def test_distill_returns_candidate_record() -> None:
    response = json.dumps(
        {
            "worth_saving": True,
            "skill_id": "kv-cache-compare",
            "name": "Compare KV-cache compression",
            "description": "desc",
            "trigger_conditions": [{"keywords": ["kv cache"], "priority": 1}],
            "procedure": "steps",
            "required_tools": ["model_block_run"],
        }
    )
    record = await distill_candidate_record(
        {"input": "q", "result": "a"},
        _router(response),
        available_tools=["model_block_run", "literature_search"],
        existing_skill_ids=[],
        existing_versions=["1.0.0"],
        source_task_id="task-1",
    )
    assert record is not None
    assert record.status == "candidate"
    assert record.version == "1.1.0"
    assert record.source_task_ids == ["task-1"]


async def test_distill_not_worth_saving_returns_none() -> None:
    record = await distill_candidate_record(
        {"input": "q"},
        _router(json.dumps({"worth_saving": False})),
        available_tools=[],
        existing_skill_ids=[],
        existing_versions=[],
        source_task_id="task-1",
    )
    assert record is None


async def test_distill_rejects_unknown_tools_and_bad_json() -> None:
    bad_tools = json.dumps(
        {
            "worth_saving": True,
            "skill_id": "x-skill",
            "name": "n",
            "procedure": "steps",
            "required_tools": ["hack_the_planet"],
        }
    )
    with pytest.raises(DistillError, match="unknown tools"):
        await distill_candidate_record(
            {},
            _router(bad_tools),
            available_tools=["model_block_run"],
            existing_skill_ids=[],
            existing_versions=[],
            source_task_id="task-1",
        )

    with pytest.raises(DistillError, match="not a JSON object"):
        await distill_candidate_record(
            {},
            _router("no json here"),
            available_tools=[],
            existing_skill_ids=[],
            existing_versions=[],
            source_task_id="task-1",
        )

    bad_schema = json.dumps(
        {"worth_saving": True, "skill_id": "BAD ID", "name": "n", "procedure": "s"}
    )
    with pytest.raises(DistillError, match="schema validation"):
        await distill_candidate_record(
            {},
            _router(bad_schema),
            available_tools=[],
            existing_skill_ids=[],
            existing_versions=[],
            source_task_id="task-1",
        )


# --- evaluate -------------------------------------------------------------


def _block_registry() -> ModelBlockRegistry:
    registry = ModelBlockRegistry()
    registry.register(KvCacheSimBlock())
    return registry


async def test_evaluator_replay_pass_and_fail() -> None:
    evaluator = SkillEvaluator(model_block_registry=_block_registry())
    record = _record()

    passing = EvalCase(
        case_id="c1",
        query="How does kv cache compression compare across ratios?",
        expected_answer_contains=["kv-cache procedure steps"],
        expect_skill_matched="kv-cache-compare",
        expect_model_blocks=["kv_cache_sim"],
    )
    result = await evaluator.evaluate(record, [passing])
    assert result.total_cases == 1
    assert result.passed_cases == 1
    assert result.eval_pass_rate == 1.0

    failing = EvalCase(
        case_id="c2",
        query="unrelated cooking question",
        expect_skill_matched="kv-cache-compare",
    )
    result = await evaluator.evaluate(record, [failing])
    assert result.passed_cases == 0
    assert result.details[0]["failures"]


async def test_evaluator_detects_regression_against_baseline() -> None:
    evaluator = SkillEvaluator()
    record = _record()
    baseline = _record(version="1.0.0", status="approved", eval_pass_rate=0.9)
    failing = EvalCase(case_id="c1", query="unrelated", expected_answer_contains=["nope"])

    result = await evaluator.evaluate(record, [failing], baseline=baseline)
    assert result.compared_to_version == "1.0.0"
    assert result.regression_detected is True
