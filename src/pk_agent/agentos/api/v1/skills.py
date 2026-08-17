from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pk_agent.agentos.dependencies import enforce_rate_limit
from pk_agent.security.tenant import Principal
from pk_agent.skills.distill import DistillError, distill_candidate_record
from pk_agent.skills.evaluate import SkillEvaluator
from pk_agent.skills.models import EvalCase, SkillRecord, SkillStatus
from pk_agent.skills.store import (
    SkillNotFoundError,
    SkillStore,
    SkillTransitionError,
)

router = APIRouter()

# A skill must demonstrate at least one passing replay case before a human
# can approve it; this blocks approving untested or fully-failing candidates.
MIN_APPROVAL_PASS_RATE = 1e-9


class DistillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=100)


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvalCase] = Field(min_length=1, max_length=20)


def _skill_store(request: Request) -> SkillStore:
    store = getattr(request.app.state, "skill_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="skill subsystem unavailable")
    return store


def _record_view(record: SkillRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


@router.get("")
async def list_approved_skills(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> list[dict[str, Any]]:
    """Latest approved version of every skill family (runtime registry view)."""
    registry = getattr(request.app.state, "skill_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="skill subsystem unavailable")
    return [_record_view(record) for record in registry.approved()]


@router.get("/pipeline")
async def list_skill_pipeline(
    request: Request,
    status: SkillStatus = "candidate",
    principal: Principal = Depends(enforce_rate_limit),
) -> list[dict[str, Any]]:
    """Skills in the pre-approval pipeline (candidate/testing/deprecated)."""
    if status == "approved":
        raise HTTPException(
            status_code=400, detail="use GET /api/v1/skills for approved skills"
        )
    store = _skill_store(request)
    records = await store.list_by_status(status)
    return [_record_view(record) for record in records]


@router.get("/{skill_id}")
async def get_skill_family(
    skill_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> list[dict[str, Any]]:
    store = _skill_store(request)
    family = await store.list_family(skill_id)
    if not family:
        raise HTTPException(status_code=404, detail="skill not found")
    return [_record_view(record) for record in family]


@router.post("/distill", status_code=201)
async def distill_skill(
    body: DistillRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> dict[str, Any]:
    """Distill a candidate skill from a completed task.

    The result is ALWAYS a candidate — this endpoint cannot approve anything;
    approval is a separate human action (POST .../approve).
    """
    store = _skill_store(request)
    task = await request.app.state.task_store.get(body.task_id)
    if task is None or task.get("tenant_id") != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") != "completed":
        raise HTTPException(status_code=409, detail="task is not completed")

    registry = request.app.state.skill_registry
    family_ids = [record.skill_id for record in registry.approved()]
    existing_versions = [
        record.version
        for skill_id in family_ids
        for record in await store.list_family(skill_id)
    ]
    try:
        record = await distill_candidate_record(
            task,
            request.app.state.model_router,
            available_tools=request.app.state.tool_registry.names(),
            existing_skill_ids=family_ids,
            existing_versions=existing_versions,
            source_task_id=body.task_id,
        )
    except DistillError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        return {"worth_saving": False, "record": None}
    await store.put_record(record)
    return {"worth_saving": True, "record": _record_view(record)}


@router.post("/{skill_id}/{version}/evaluate")
async def evaluate_skill(
    skill_id: str,
    version: str,
    body: EvaluateRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> dict[str, Any]:
    """Offline deterministic replay of eval cases; moves candidate -> testing."""
    store = _skill_store(request)
    try:
        record = await store.get_record(skill_id, version)
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    if record.status not in ("candidate", "testing"):
        raise HTTPException(
            status_code=409,
            detail=f"cannot evaluate a {record.status} skill",
        )

    evaluator = SkillEvaluator(
        model_block_registry=getattr(request.app.state, "model_block_registry", None),
        judge_router=getattr(request.app.state, "model_router", None),
    )
    result = await evaluator.evaluate(record, body.cases)
    await store.put_eval_run(result.run_id, skill_id, version, result)

    if record.status == "candidate":
        updated = await store.transition(
            skill_id, version, "testing", eval_pass_rate=result.eval_pass_rate
        )
    else:
        updated = record.model_copy(update={"eval_pass_rate": result.eval_pass_rate})
        await store.put_record(updated)
    return {
        "eval_result": result.model_dump(mode="json"),
        "record": _record_view(updated),
    }


@router.post("/{skill_id}/{version}/approve")
async def approve_skill(
    skill_id: str,
    version: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> dict[str, Any]:
    """Human gate: testing -> approved. Publishes the skill to the registry."""
    store = _skill_store(request)
    try:
        record = await store.get_record(skill_id, version)
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    if record.eval_pass_rate < MIN_APPROVAL_PASS_RATE and record.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="skill has no passing evaluation; run POST .../evaluate first",
        )
    try:
        updated = await store.transition(
            skill_id,
            version,
            "approved",
            approved_by=f"{principal.tenant_id}:{principal.user_id}",
        )
    except SkillTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.skill_registry.refresh()
    return _record_view(updated)


@router.post("/{skill_id}/{version}/deprecate")
async def deprecate_skill(
    skill_id: str,
    version: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> dict[str, Any]:
    store = _skill_store(request)
    try:
        updated = await store.transition(skill_id, version, "deprecated")
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill not found") from None
    except SkillTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.skill_registry.refresh()
    return _record_view(updated)
