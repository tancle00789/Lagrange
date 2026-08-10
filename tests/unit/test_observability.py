from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from fastapi import Request, Response

from pk_agent.config import Settings
from pk_agent.observability import (
    JsonLogFormatter,
    Observability,
    request_id_context,
    task_context,
    tenant_context,
)


def test_json_logs_add_correlation_and_redact_credentials() -> None:
    request_token = request_id_context.set("request-1")
    tenant_token = tenant_context.set("tenant-hash")
    task_token = task_context.set("task-1")
    try:
        record = logging.LogRecord(
            "test",
            logging.INFO,
            __file__,
            1,
            "authorization Bearer top-secret token=also-secret",
            (),
            None,
        )
        record.api_key = "do-not-log"
        body = json.loads(JsonLogFormatter().format(record))
    finally:
        task_context.reset(task_token)
        tenant_context.reset(tenant_token)
        request_id_context.reset(request_token)

    assert body["request_id"] == "request-1"
    assert body["tenant_hash"] == "tenant-hash"
    assert body["task_id"] == "task-1"
    assert "top-secret" not in body["message"]
    assert "also-secret" not in body["message"]
    assert body["message"].count("[REDACTED]") == 2
    assert body["api_key"] == "[REDACTED]"


async def test_http_middleware_emits_bounded_route_metrics_and_request_id() -> None:
    observability = Observability(Settings())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/tasks/private-id",
        "query_string": b"",
        "headers": [(b"x-request-id", b"caller-request")],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "root_path": "",
        "route": SimpleNamespace(path="/tasks/{task_id}"),
    }
    request = Request(scope)

    async def call_next(request: Request) -> Response:
        return Response("ok", status_code=202)

    response = await observability.http_middleware(request, call_next)
    metrics = observability.metrics_response().body.decode()

    assert response.headers["x-request-id"] == "caller-request"
    assert 'route="/tasks/{task_id}"' in metrics
    assert "private-id" not in metrics
    assert 'status="202"' in metrics
    observability.shutdown()


def test_trace_context_propagates_into_job_carrier_and_job_metrics() -> None:
    observability = Observability(Settings())
    with observability.tracer.start_as_current_span("submit"):
        carrier = observability.inject_trace_context()
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "traced", (), None)
        traced_log = json.loads(JsonLogFormatter().format(record))

    assert carrier["traceparent"].startswith("00-")
    assert len(traced_log["trace_id"]) == 32
    assert len(traced_log["span_id"]) == 16
    with observability.job_span("task-a", "tenant-a", carrier):
        observability.record_job("completed")
    metrics = observability.metrics_response().body.decode()
    assert 'pk_agent_jobs_total{status="completed"} 1.0' in metrics
    assert "pk_agent_active_jobs 0.0" in metrics
    observability.shutdown()
