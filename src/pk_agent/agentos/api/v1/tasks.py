from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from pk_agent.agentos.dependencies import enforce_rate_limit
from pk_agent.agentos.job_queue import JobEnvelope, RetryableJobError
from pk_agent.models.io import (
    ResumeRequest,
    SseTicketResponse,
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
        model_runs=record.get("model_runs", []),
        messages=record.get("messages", []),
    )


@router.get("", response_model=list[TaskStatusResponse])
async def list_tasks(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[TaskStatusResponse]:
    records = await request.app.state.task_store.list_for_tenant(principal.tenant_id, limit=limit)
    return [_record_response(record) for record in records]


async def _owned_record(request: Request, task_id: str, tenant_id: str) -> dict[str, Any]:
    record = await request.app.state.task_store.get(task_id)
    if record is None or record["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    return record


async def execute_job(app: Any, job: JobEnvelope) -> None:
    tenant_id = str(job.initial_state.get("tenant_id", ""))
    observability = getattr(app.state, "observability", None)
    if observability is None:
        await _execute_job(app, job)
        return
    with observability.job_span(job.task_id, tenant_id, job.trace_context):
        try:
            await _execute_job(app, job)
        finally:
            record = await app.state.task_store.get(job.task_id)
            observability.record_job(str((record or {}).get("status", "missing")))


async def _execute_job(app: Any, job: JobEnvelope) -> None:
    task_id = job.task_id
    current = asyncio.current_task()
    if current is not None:
        app.state.task_runs[task_id] = current
    try:
        record = await app.state.task_store.get(task_id)
        if record is None or record.get("status") in {"cancelled", "completed", "failed"}:
            return
        configurable = job.config.get("configurable", {})
        if (
            job.initial_state.get("tenant_id") != record.get("tenant_id")
            or configurable.get("tenant_id") != record.get("tenant_id")
            or configurable.get("thread_id") != record.get("thread_id")
        ):
            await app.state.event_bus.publish(
                task_id,
                {"type": "security_rejected", "reason": "job tenant boundary mismatch"},
            )
            return
        attempt = int(record.get("attempts", 0)) + 1
        running = await app.state.task_store.update(
            task_id,
            {"status": "running", "attempts": attempt, "result": None},
            expected_statuses={"pending", "retrying", "running"},
        )
        if running is None or running["status"] != "running":
            return
        try:
            result = await app.state.graph.ainvoke(job.initial_state, config=job.config)
            await app.state.task_store.update(
                task_id,
                {
                    "status": result.get("status", "completed"),
                    "result": result.get("result"),
                    "report": result.get("report"),
                    "knowledge_hits": result.get("knowledge_hits", []),
                    "external_sources": result.get("external_sources", []),
                    "model_runs": result.get("model_runs", []),
                    "messages": result.get("messages", []),
                },
                expected_statuses={"running"},
            )
        except asyncio.CancelledError:
            if app.state.shutting_down:
                await app.state.task_store.update(
                    task_id,
                    {"status": "retrying", "result": "worker interrupted; awaiting lease"},
                    expected_statuses={"running"},
                )
            else:
                await app.state.task_store.update(
                    task_id,
                    {"status": "cancelled"},
                    expected_statuses={"pending", "retrying", "running"},
                )
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            if attempt < app.state.settings.worker_max_attempts:
                await app.state.task_store.update(
                    task_id,
                    {"status": "retrying", "result": f"attempt {attempt} failed: {exc}"},
                    expected_statuses={"running"},
                )
                await app.state.event_bus.publish(
                    task_id,
                    {"type": "retrying", "attempt": attempt, "reason": str(exc)},
                )
                raise RetryableJobError(str(exc)) from exc
            updated = await app.state.task_store.update(
                task_id,
                {"status": "failed", "result": str(exc)},
                expected_statuses={"running"},
            )
            if updated is not None and updated["status"] == "failed":
                await app.state.event_bus.publish(task_id, {"type": "failed", "reason": str(exc)})
    finally:
        if current is not None and app.state.task_runs.get(task_id) is current:
            app.state.task_runs.pop(task_id, None)


@router.post("", response_model=TaskCreateResponse, status_code=202)
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskCreateResponse:
    require_role(principal, "admin")
    app = request.app

    if idempotency_key and len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="idempotency key is too long")
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
        "model_runs": [],
        "messages": [],
        "attempts": 0,
        "created_at": time.time(),
    }
    stored, created = await app.state.task_store.create(record, idempotency_key=idempotency_key)
    if not created:
        return TaskCreateResponse(task_id=stored["task_id"], status=stored["status"])

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
            "tenant_id": principal.tenant_id,
            "checkpoint_ns": app.state.settings.checkpoint_ns,
        }
    }

    observability = getattr(app.state, "observability", None)
    trace_context = observability.inject_trace_context() if observability is not None else {}
    job = JobEnvelope(
        task_id=task_id,
        initial_state=initial_state,
        config=config,
        trace_context=trace_context,
    )
    if app.state.job_queue is not None:
        await app.state.job_queue.submit(job)
    else:
        async def run_local() -> None:
            while True:
                try:
                    await execute_job(app, job)
                    return
                except RetryableJobError:
                    current = asyncio.current_task()
                    if current is not None:
                        app.state.task_runs[task_id] = current
                    await asyncio.sleep(app.state.settings.worker_retry_backoff_seconds)

        background = asyncio.create_task(run_local(), name=f"pk-agent-task-{task_id}")
        app.state.task_runs[task_id] = background
    return TaskCreateResponse(task_id=task_id, status="pending")


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> TaskStatusResponse:
    record = await _owned_record(request, task_id, principal.tenant_id)
    return _record_response(record)


@router.post("/{task_id}/events/ticket", response_model=SseTicketResponse)
async def create_event_ticket(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> SseTicketResponse:
    await _owned_record(request, task_id, principal.tenant_id)
    ticket = await request.app.state.sse_tickets.issue(principal, task_id)
    return SseTicketResponse(
        ticket=ticket,
        expires_in=request.app.state.settings.sse_ticket_ttl_seconds,
    )


@router.get("/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    ticket: str = Query(min_length=32, max_length=128),
) -> EventSourceResponse:
    record = await request.app.state.task_store.get(task_id)
    credential = await request.app.state.sse_tickets.consume(ticket, task_id)
    if record is None or credential is None or record["tenant_id"] != credential.tenant_id:
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
    record = await _owned_record(request, task_id, principal.tenant_id)
    if record["status"] != "paused":
        return _record_response(record)
    if not payload.approved:
        updated = await request.app.state.task_store.update(
            task_id,
            {"status": "cancelled"},
            expected_statuses={"paused"},
        )
        if updated is not None and updated["status"] == "cancelled":
            await request.app.state.event_bus.publish(
                task_id, {"type": "cancelled", "reason": payload.note}
            )
    else:
        messages = list(record["messages"])
        messages.append({"role": "reviewer", "content": payload.note or "approved"})
        updated = await request.app.state.task_store.update(
            task_id,
            {"status": "completed", "messages": messages},
            expected_statuses={"paused"},
        )
        if updated is not None and updated["status"] == "completed":
            await request.app.state.event_bus.publish(
                task_id, {"type": "completed", "approved": True}
            )
    return _record_response(updated or record)


@router.delete("/{task_id}", response_model=TaskStatusResponse)
async def cancel_task(
    task_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> TaskStatusResponse:
    record = await _owned_record(request, task_id, principal.tenant_id)
    if record["status"] in {"pending", "running", "paused"}:
        updated = await request.app.state.task_store.update(
            task_id,
            {"status": "cancelled"},
            expected_statuses={"pending", "running", "paused"},
        )
        if updated is not None and updated["status"] == "cancelled":
            background = request.app.state.task_runs.get(task_id)
            if background is not None and not background.done():
                background.cancel()
            await request.app.state.cancellation.request(task_id)
            await request.app.state.event_bus.publish(task_id, {"type": "cancelled"})
        return _record_response(updated or record)
    return _record_response(record)
