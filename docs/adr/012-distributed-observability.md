# ADR 012 — Correlated Logs, Metrics and Distributed Traces

Status: accepted (Phase 09)
Date: 2026-08-05

## Context

Redis made requests and jobs independent of a single replica, but plain logs
could not connect an API submission to a worker execution. There was also no
stable metrics contract for latency, errors, task outcomes or active work.

## Decision

- Every HTTP request receives or validates an `X-Request-ID`; the response
  echoes it. Logs are JSON and automatically include request ID, hashed tenant,
  task ID, trace ID and span ID when those contexts exist.
- Log messages redact bearer credentials and token/key/secret query values.
  Structured fields with sensitive names are replaced before serialization.
- Each app owns a Prometheus registry exposed at `/metrics`. Labels use bounded
  route templates rather than raw paths/task IDs. Metrics cover HTTP count and
  duration, job outcome and duration, and active jobs.
- OpenTelemetry spans cover HTTP requests and worker execution. W3C trace
  context is injected into the durable job envelope, so another replica can
  continue the submission trace. An OTLP/HTTP exporter is enabled only when
  `PK_AGENT_OTLP_ENDPOINT` is configured.
- Prometheus scrape and alert rules are versioned in `ops/`. Metrics endpoints
  are intended for an internal network or ingress policy, not public exposure.

## Consequences

- Operators can move from an alert to a route, trace and task without logging
  bearer tokens or raw tenant identifiers.
- Metrics are per replica and designed to be aggregated by Prometheus. Trace
  sampling/storage policy remains the responsibility of the configured
  collector/backend.
- Cardinality is bounded in metric labels; task and request identifiers remain
  in logs/traces where high-cardinality correlation belongs.

