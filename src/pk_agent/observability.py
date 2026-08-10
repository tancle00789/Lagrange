from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from pk_agent.config import Settings

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
tenant_context: contextvars.ContextVar[str] = contextvars.ContextVar("tenant", default="")
task_context: contextvars.ContextVar[str] = contextvars.ContextVar("task", default="")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"((?:token|api[_-]?key|secret)=)[^&\s]+"
)
_SENSITIVE_FIELD_RE = re.compile(r"(?i)(authorization|token|api[_-]?key|secret)")
_STANDARD_LOG_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _tenant_hash(tenant_id: str) -> str:
    return hashlib.sha256(tenant_id.encode()).hexdigest()[:16]


def bind_tenant(tenant_id: str) -> None:
    hashed = _tenant_hash(tenant_id)
    tenant_context.set(hashed)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("tenant.hash", hashed)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        message = _SECRET_RE.sub(
            lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", message
        )
        body: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for key, value in (
            ("request_id", request_id_context.get()),
            ("tenant_hash", tenant_context.get()),
            ("task_id", task_context.get()),
        ):
            if value:
                body[key] = value
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            body["trace_id"] = format(span_context.trace_id, "032x")
            body["span_id"] = format(span_context.span_id, "016x")
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and key not in {"message", "asctime"}:
                body[key] = "[REDACTED]" if _SENSITIVE_FIELD_RE.search(key) else value
        if record.exc_info:
            body["exception"] = self.formatException(record.exc_info)
        return json.dumps(body, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_json_logging(level: str) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


class Observability:
    def __init__(self, settings: Settings) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "pk_agent_http_requests_total",
            "HTTP requests by method, route and status.",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "pk_agent_http_request_duration_seconds",
            "HTTP request latency.",
            ("method", "route"),
            registry=self.registry,
        )
        self.jobs = Counter(
            "pk_agent_jobs_total",
            "Job executions by terminal observation.",
            ("status",),
            registry=self.registry,
        )
        self.job_duration = Histogram(
            "pk_agent_job_duration_seconds",
            "Job execution latency per attempt.",
            registry=self.registry,
        )
        self.active_jobs = Gauge(
            "pk_agent_active_jobs",
            "Jobs currently executing in this replica.",
            registry=self.registry,
        )
        self.metrics_enabled = settings.metrics_enabled
        self.provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.observability_service_name}),
            shutdown_on_exit=False,
        )
        if settings.otlp_endpoint:
            exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
            self.provider.add_span_processor(BatchSpanProcessor(exporter))
        self.tracer = self.provider.get_tracer("pk_agent")

    async def http_middleware(self, request: Request, call_next: Any) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid4().hex
        request_token = request_id_context.set(request_id)
        tenant_token = tenant_context.set("")
        task_token = task_context.set("")
        started = time.perf_counter()
        status_code = 500
        with self.tracer.start_as_current_span(
            f"{request.method} {request.url.path}", kind=SpanKind.SERVER
        ) as span:
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("url.path", request.url.path)
            span.set_attribute("request.id", request_id)
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers["X-Request-ID"] = request_id
                return response
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                elapsed = time.perf_counter() - started
                self.http_requests.labels(request.method, route, str(status_code)).inc()
                self.http_duration.labels(request.method, route).observe(elapsed)
                span.set_attribute("http.response.status_code", status_code)
                span.set_attribute("http.route", route)
                logging.getLogger("pk_agent.http").info(
                    "request completed",
                    extra={
                        "event": "http_request",
                        "method": request.method,
                        "route": route,
                        "status": status_code,
                        "duration_ms": round(elapsed * 1_000, 3),
                    },
                )
                task_context.reset(task_token)
                tenant_context.reset(tenant_token)
                request_id_context.reset(request_token)

    @contextmanager
    def job_span(
        self,
        task_id: str,
        tenant_id: str,
        trace_context: dict[str, str],
    ) -> Iterator[None]:
        extracted = propagate.extract(trace_context)
        task_token = task_context.set(task_id)
        tenant_token = tenant_context.set(_tenant_hash(tenant_id))
        started = time.perf_counter()
        self.active_jobs.inc()
        with self.tracer.start_as_current_span(
            "agent.job.execute", context=extracted, kind=SpanKind.CONSUMER
        ) as span:
            span.set_attribute("task.id", task_id)
            span.set_attribute("tenant.hash", _tenant_hash(tenant_id))
            try:
                yield
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                self.active_jobs.dec()
                self.job_duration.observe(time.perf_counter() - started)
                tenant_context.reset(tenant_token)
                task_context.reset(task_token)

    def record_job(self, status: str) -> None:
        self.jobs.labels(status).inc()

    def inject_trace_context(self) -> dict[str, str]:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        return carrier

    def metrics_response(self) -> Response:
        if not self.metrics_enabled:
            return Response(status_code=404)
        return Response(generate_latest(self.registry), media_type="text/plain; version=0.0.4")

    def shutdown(self) -> None:
        self.provider.shutdown()
