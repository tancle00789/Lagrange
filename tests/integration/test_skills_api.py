from __future__ import annotations

import asyncio
import json

from pk_agent.harness.router import FakeModel
from pk_agent.skills.models import SkillRecord, TriggerCondition

AUTH = {"Authorization": "Bearer dev:tenant-a:user-1:admin"}
OTHER_AUTH = {"Authorization": "Bearer dev:tenant-b:user-2:admin"}


def _candidate(**overrides) -> SkillRecord:
    payload = {
        "skill_id": "kv-cache-compare",
        "version": "1.0.0",
        "status": "candidate",
        "name": "Compare KV-cache compression",
        "description": "desc",
        "trigger_conditions": [TriggerCondition(keywords=["kv cache"], priority=1)],
        "procedure": "kv-cache procedure steps",
        "required_tools": ["model_block_run"],
    }
    payload.update(overrides)
    return SkillRecord(**payload)


def _seed(client, record: SkillRecord) -> None:
    asyncio.run(client.app.state.skill_store.put_record(record))


def _eval_case() -> dict:
    return {
        "case_id": "c1",
        "query": "How does kv cache compression compare across ratios?",
        "expected_answer_contains": ["kv-cache procedure steps"],
        "expect_skill_matched": "kv-cache-compare",
    }


def test_skills_endpoints_require_auth(client) -> None:
    assert client.get("/api/v1/skills").status_code == 401
    assert client.post("/api/v1/skills/distill", json={"task_id": "t"}).status_code == 401


def test_approve_gate_requires_passing_evaluation(client) -> None:
    _seed(client, _candidate())

    # Candidate -> approved directly is an illegal transition.
    response = client.post("/api/v1/skills/kv-cache-compare/1.0.0/approve", headers=AUTH)
    assert response.status_code == 409

    # Even after reaching testing status, a 0% pass rate cannot be approved.
    store = client.app.state.skill_store
    asyncio.run(store.transition("kv-cache-compare", "1.0.0", "testing"))
    response = client.post("/api/v1/skills/kv-cache-compare/1.0.0/approve", headers=AUTH)
    assert response.status_code == 409


def test_full_lifecycle_evaluate_approve_deprecate(client) -> None:
    _seed(client, _candidate())

    response = client.post(
        "/api/v1/skills/kv-cache-compare/1.0.0/evaluate",
        headers=AUTH,
        json={"cases": [_eval_case()]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eval_result"]["eval_pass_rate"] == 1.0
    assert body["record"]["status"] == "testing"

    response = client.post("/api/v1/skills/kv-cache-compare/1.0.0/approve", headers=AUTH)
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "tenant-a:user-1"

    listed = client.get("/api/v1/skills", headers=AUTH).json()
    assert [record["skill_id"] for record in listed] == ["kv-cache-compare"]

    # Approval publishes into the runtime registry (refresh happened inline).
    registry = client.app.state.skill_registry
    assert registry.get("kv-cache-compare") is not None

    response = client.post(
        "/api/v1/skills/kv-cache-compare/1.0.0/deprecate", headers=AUTH
    )
    assert response.status_code == 200
    assert client.get("/api/v1/skills", headers=AUTH).json() == []
    assert registry.get("kv-cache-compare") is None


def test_distill_from_completed_task(client) -> None:
    asyncio.run(
        client.app.state.task_store.create(
            {
                "task_id": "task-distill",
                "tenant_id": "tenant-a",
                "status": "completed",
                "input": "compare kv cache ratios",
                "result": "8:1 beats 4:1 on throughput",
                "model_runs": [
                    {"block_name": "kv_cache_sim", "status": "completed", "metrics": {}}
                ],
            }
        )
    )
    distill_response = json.dumps(
        {
            "worth_saving": True,
            "skill_id": "kv-cache-compare",
            "name": "Compare KV-cache compression",
            "description": "desc",
            "trigger_conditions": [{"keywords": ["kv cache"], "priority": 1}],
            "procedure": "kv-cache procedure steps",
            # In-memory test runtime has no model-block broker, so only the
            # literature tools are registered; use one of those.
            "required_tools": ["literature_search"],
            "eval_cases": [
                {
                    "case_id": "case-1",
                    "query": "how does kv cache compression work",
                    "expected_answer_contains": ["kv-cache procedure steps"],
                }
            ],
        }
    )
    client.app.state.model_router.register("distill", FakeModel(response=distill_response))

    response = client.post(
        "/api/v1/skills/distill", headers=AUTH, json={"task_id": "task-distill"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["worth_saving"] is True
    record = body["record"]
    assert record["status"] == "candidate"
    assert record["source_task_ids"] == ["task-distill"]
    # Suggested cases come back pinned to the distilled skill id.
    assert body["eval_cases"][0]["expect_skill_matched"] == "kv-cache-compare"

    pipeline = client.get("/api/v1/skills/pipeline", headers=AUTH).json()
    assert [item["skill_id"] for item in pipeline] == ["kv-cache-compare"]


def test_distill_rejects_incomplete_and_cross_tenant_tasks(client) -> None:
    asyncio.run(
        client.app.state.task_store.create(
            {"task_id": "task-running", "tenant_id": "tenant-a", "status": "running"}
        )
    )
    asyncio.run(
        client.app.state.task_store.create(
            {"task_id": "task-other", "tenant_id": "tenant-b", "status": "completed"}
        )
    )
    response = client.post(
        "/api/v1/skills/distill", headers=AUTH, json={"task_id": "task-running"}
    )
    assert response.status_code == 409
    response = client.post(
        "/api/v1/skills/distill", headers=AUTH, json={"task_id": "task-other"}
    )
    assert response.status_code == 404
    response = client.post(
        "/api/v1/skills/distill", headers=AUTH, json={"task_id": "missing"}
    )
    assert response.status_code == 404


def test_evaluate_missing_skill_404(client) -> None:
    response = client.post(
        "/api/v1/skills/nope/1.0.0/evaluate",
        headers=AUTH,
        json={"cases": [_eval_case()]},
    )
    assert response.status_code == 404
    response = client.post("/api/v1/skills/nope/1.0.0/approve", headers=AUTH)
    assert response.status_code == 404
