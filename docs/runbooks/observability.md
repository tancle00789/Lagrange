# Observability Operations

## Signals

- Metrics: `GET /metrics` when `PK_AGENT_METRICS_ENABLED=true`.
- Traces: set `PK_AGENT_OTLP_ENDPOINT` to the collector's full HTTP traces
  endpoint, commonly `http://otel-collector:4318/v1/traces`.
- Logs: JSON on stdout; ingest with the container platform's normal log agent.

The supplied `ops/prometheus.yml` scrapes `app:8000`, and
`ops/prometheus-alerts.yml` covers target loss, 5xx ratio, p95 latency, failed
jobs and long-running jobs. Tune thresholds against an observed baseline.

## Correlation workflow

1. Start with alert labels and the affected time window.
2. Find HTTP logs by `route` and `status`.
3. Follow `trace_id` from the API server span to `agent.job.execute`, even when
   a different replica consumed the Redis Stream message.
4. Use `task_id` to inspect the durable task record and Redis pending entry.
5. Use the hashed tenant value for correlation; do not add raw tenant IDs to
   metric labels.

## Security and failure behavior

Protect `/metrics` at the network/ingress layer. It contains no task inputs or
raw tenant IDs but exposes operational topology. OTLP export is batched and
optional; an unconfigured collector does not affect task execution. Never add
authorization headers, SSE tickets, model prompts or evidence content as span
attributes.

