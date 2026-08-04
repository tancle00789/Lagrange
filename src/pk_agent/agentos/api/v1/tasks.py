from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from pk_agent.agentos.dependencies import enforce_rate_limit
from pk_agent.models.io import (
    ResumeRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskStatusResponse,
)
from pk_agent.security.rbac import require_role
from pk_agent.security.tenant import Principal

router = APIRouter()


def _record_response(record: dict[str, Any]) -> TaskStatusResponse:
    return TaskStatusResponse(
        task_id=record["task_id"],
        tenant_id=record["tenant_id"],
        status=record["status"],
        result=record.get("result"),
        report=record.get("report"),
        knowledge_hits=record.get("knowledge_hits", []),
        external_sources=record.get("external_sources", []),
        messages=record.get("messages", []),
    )


@router.get("", response_model=list[TaskStatusResponse])
async def list_tasks(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
    limit: int = 100,
) -> list[TaskStatusResponse]:
    records = [
        record
        for record in request.app.state.tasks.values()
        if record["tenant_id"] == principal.tenant_id
    ]
    records.sort(key=lambda record: record.get("created_at", 0), reverse=True)
    return [_record_response(record) for record in records[:limit]]


@router.post("", response_model=TaskCreateResponse, status_code=202)
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskCreateResponse:
    require_role(principal, "admin")
    app = request.app

    if idempotency_key and idempotency_key in app.state.idempotency:
        task_id = app.state.idempotency[idempotency_key]
        return TaskCreateResponse(task_id=task_id, status=app.state.tasks[task_id]["status"])

    task_id = uuid4().hex
    thread_id = f"{principal.tenant_id}:{task_id}"
    record: dict[str, Any] = {
        "task_id": task_id,
        "tenant_id": principal.tenant_id,
        "thread_id": thread_id,
        "status": "pending",
        "result": None,
        "report": None,
        "knowledge_hits": [],
        "external_sources": [],
        "messages": [],
        "created_at": time.time(),
    }
    app.state.tasks[task_id] = record
    if idempotency_key:
        app.state.idempotency[idempotency_key] = task_id

    initial_state = {
        "task_id": task_id,
        "tenant_id": principal.tenant_id,
        "status": "pending",
        "input": payload.input,
        "require_approval": payload.require_approval,
        "approved": False,
        "allow_external": payload.allow_external,
        "ingest_external": payload.ingest_external,
        "budget_calls": 0,
        "messages": [{"role": "user", "content": payload.input}],
    }
    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": app.state.settings.checkpoint_ns,
        }
    }

    async def run() -> None:
        record["status"] = "running"
        try:
            result = await app.state.graph.ainvoke(initial_state, config=config)
            record.update(
                status=result.get("status", "completed"),
                result=result.get("result"),
                report=result.get("report"),
                knowledge_hits=result.get("knowledge_hits", []),
                external_sources=result.get("external_sources", []),
                messages=result.get("messages", []),
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            record.update(status="failed", result=str(exc))
            await app.state.event_bus.publish(task_id, {"type": "failed", "reason": str(exc)})

    asyncio.create_task(run())
    return TaskCreateResponse(task_id=task_id, status="pending")


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> TaskStatusResponse:
    record = request.app.state.tasks.get(task_id)
    if record is None or record["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    return _record_response(record)


@router.get("/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> EventSourceResponse:
    record = request.app.state.tasks.get(task_id)
    if record is None or record["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")

    async def generator():
        yield {"event": "snapshot", "data": json.dumps(_record_response(record).model_dump())}
        async for event in request.app.state.event_bus.subscribe(task_id):
            yield {"event": str(event.get("type", "message")), "data": json.dumps(event)}
            if event.get("type") in {"completed", "failed", "cancelled", "paused"}:
                break

    return EventSourceResponse(generator())


@router.post("/{task_id}/resume", response_model=TaskStatusResponse)
async def resume_task(
    task_id: str,
    payload: ResumeRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> TaskStatusResponse:
    record = request.app.state.tasks.get(task_id)
    if record is None or record["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    if record["status"] != "paused":
        return _record_response(record)
    if not payload.approved:
        record["status"] = "cancelled"
        await request.app.state.event_bus.publish(
            task_id, {"type": "cancelled", "reason": payload.note}
        )
    else:
        record["status"] = "completed"
        record["messages"].append({"role": "reviewer", "content": payload.note or "approved"})
        await request.app.state.event_bus.publish(task_id, {"type": "completed", "approved": True})
    return _record_response(record)


@router.delete("/{task_id}", response_model=TaskStatusResponse)
async def cancel_task(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> TaskStatusResponse:
    record = request.app.state.tasks.get(task_id)
    if record is None or record["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    if record["status"] in {"pending", "running", "paused"}:
        record["status"] = "cancelled"
        await request.app.state.event_bus.publish(task_id, {"type": "cancelled"})
    return _record_response(record)
