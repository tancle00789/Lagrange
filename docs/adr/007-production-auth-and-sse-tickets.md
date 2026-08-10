# ADR 007 — Production Authentication and SSE Tickets

Status: accepted (Phase 05)
Date: 2026-08-05

## Context

The browser used a long-lived bearer token in the EventSource query string
because the native EventSource API cannot set an Authorization header. Query
credentials can be retained by browser history, access logs and reverse
proxies. The authentication parser also accepted development tokens in every
environment, accepted signed tokens without an expiry, and used a default JWT
secret unless operators changed it.

Task idempotency keys were stored globally rather than under a tenant scope,
so two tenants using the same key could resolve to the first tenant's task.
The subsequent task read was still tenant-checked, but the creation response
could disclose the other task identifier.

## Decision

- `dev:{tenant}:{user}:{roles}` bearer tokens are accepted only outside the
  `production` environment. Production startup rejects the default JWT secret
  and secrets shorter than 32 characters.
- HS256 tokens must contain `exp`, `sub` and `tenant_id`. The tenant, subject
  and roles claim shapes are validated before a `Principal` is created.
- The authenticated client obtains an SSE ticket from
  `POST /api/v1/tasks/{task_id}/events/ticket`. The random ticket expires after
  60 seconds by default, is bound to the principal's tenant and task, and is
  removed on its first connection attempt. Only this ticket enters the SSE
  query string; the bearer token remains in an Authorization header.
- The current ticket store is process-local. This matches the current
  process-local task/event registry; both must move to shared infrastructure
  before horizontally scaling the API.
- Idempotency keys are indexed by `(tenant_id, key)`, capped at 200 characters,
  and RBAC failures return HTTP 403 rather than escaping as server errors.

## Consequences

- Existing browser clients must request a ticket before opening EventSource;
  the bundled frontend now performs this exchange automatically.
- A disconnected EventSource needs a fresh ticket rather than replaying the
  old URL. The existing task polling remains a fallback for the current UI.
- Reverse proxies should still redact the `ticket` query parameter. Its short
  lifetime and single-use property reduce exposure but do not make logging it
  desirable.
- Multi-replica deployment requires a shared task/event/ticket layer or sticky
  routing; Redis-backed coordination remains a later phase.
