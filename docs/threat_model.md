# Threat Model — Phase 09

## Assets
- Tenant task inputs and run outputs
- Checkpoint state
- Model/tool budgets
- WeKnora scoped API keys, base URL, and KB mapping
- Retrieved knowledge snippets (tenant-confidential content)
- External literature metadata (untrusted third-party content)
- GitHub token, Springer Nature API key (optional external credentials)
- JWT/dev bearer tokens

## Trust boundaries
- HTTP client -> AgentOS/FastAPI
- AgentOS -> LangGraph runtime
- LangGraph -> PostgreSQL checkpoint store
- LangGraph -> WeKnora HTTP API (Phase 2): the only path to knowledge data
- LangGraph -> external literature APIs (Phase 4'): arXiv, GitHub, CrossRef,
  Springer Nature — all content crossing this boundary is untrusted
- Later phases: object storage

## Phase 1 controls
- Bearer auth; `dev:{tenant}:{user}:{roles}` accepted only for Phase 1 development, JWT path reserved.
- Tenant isolation via `tenant_id` on task records and `{tenant_id}:{task_id}` checkpoint thread ids.
- Admin role required for task creation.
- In-memory per-tenant rate limiter.
- Idempotency-Key support for task creation.
- FakeModel only in tests; no real model calls.
- Graph state stores only small metadata, not documents, chunks, PDFs, or web pages.

## Phase 2 controls (WeKnora subsystem)
- Business code never connects to the WeKnora database; the HTTP API is the
  only interface, reachable only from the `app` container (`weknora-net`).
- WeKnora access uses scoped API keys (`X-API-Key`); the key is injected via
  env, never logged, and the client logs only method/path/status code.
- Tenant -> KB mapping is explicit config; unmapped tenants are rejected with
  an error instead of falling back to another tenant's KB.
- Snippets are truncated in the provider (default 500 chars) so checkpoint
  state and SSE events cannot exfiltrate full documents.
- Retrieval retries are bounded (3 attempts, <=8s backoff, 5xx/connection
  only) — no infinite retry loops against a degraded subsystem.
- Auth failures from WeKnora fail the task loudly; only availability/timeout
  failures degrade to empty context, and every degradation emits a
  `knowledge_degraded` event.
- Real-integration smoke (`scripts/smoke_test_weknora.sh`) is opt-in and never
  replaced by mocks passing as integration proof.

## Phase 3 controls (research workflow + web UI)
- The answer model is prompted for strict JSON, but output is never trusted:
  the graph strips code fences, falls back to plain text on parse failure, and
  the `citation_gate` node drops every chunk_id that was not actually
  retrieved, so the LLM cannot fabricate citations.
- Claim texts are truncated (300 chars) and snippets stay bounded (500 chars)
  before entering graph state — prompt-injection via retrieved content cannot
  smuggle large payloads into checkpoints.
- The browser SPA is served by the same FastAPI app (no extra trust boundary);
  CORS is open only to the Vite dev origin `http://localhost:5173`.
- EventSource cannot set headers, so SSE auth also accepts `?token=` as a
  fallback. Tokens in URLs risk leaking via proxy/access logs — acceptable
  for the dev token format, must be replaced with signed, short-lived SSE
  tickets (or cookie auth) together with the JWT work below.
- `ollama_chat_model` unset ⇒ FakeModel: tests and CI never call a real model.

## Phase 4' controls (literature subsystem)
- External retrieval runs only through the formalized `ToolRegistry`:
  explicit registration, per-call risk gating (search=L1, PDF ingest=L2),
  bounded timeouts, and retries limited to transient errors. No implicit tool
  registration, no unbounded retry.
- SSRF: PDF downloads are restricted to https URLs on the configured host
  allowlist; redirects are followed manually (max 3) and every hop is
  re-validated against the allowlist. arXiv/GitHub/CrossRef/Springer search
  endpoints are hard-coded from config, not derived from model output.
- External content is untrusted: records carry only metadata + abstract
  (≤800 chars), titles/abstracts enter prompts as evidence blocks, and the
  citation gate validates every source_id against actually-retrieved records
  — the LLM cannot fabricate external citations either.
- Copyright boundary (ADR 006): full PDFs are fetched and ingested into
  WeKnora only when the task explicitly sets `ingest_external=true`; default
  flow is metadata-only. Downloads are size-capped (25MB), content-type
  checked, and capped at 2 files per task.
- External credentials (GitHub token, Springer key) are env-injected, never
  logged; the Nature source is simply not registered when unconfigured.
- External source failures degrade per-source (recorded in `external_error`,
  surfaced as `external_degraded` events) and never fail the task — an
  optional enhancement cannot become a denial vector for the core workflow.
- arXiv's 3s rate limit is enforced client-side; CrossRef polite-pool
  mailto is configurable.
- Real-integration smoke (`scripts/smoke_test_literature.sh`) hits the real
  arXiv API, reports SKIP (exit 2) when the network is unreachable — a skip
  is never reported as a pass.

## Phase 05 controls (production authentication boundary)

- Production startup fails closed when `PK_AGENT_JWT_SECRET` is the development
  default or shorter than 32 characters. Development bearer tokens are
  rejected when `PK_AGENT_ENVIRONMENT=production`.
- Signed JWTs require expiry, subject and tenant claims; tenant, subject and
  roles claim types are checked before they become authorization context.
- Long-lived bearer tokens no longer enter EventSource URLs. An authenticated
  endpoint issues a random, task/tenant-bound SSE ticket with a 60-second
  default lifetime; every ticket is consumed on its first connection attempt.
- Task creation returns HTTP 403 for missing roles. Idempotency records are
  tenant-scoped, preventing cross-tenant key collisions and task-ID disclosure.

## Phase 06 controls (durable task control plane)

- Production refuses the process-local runtime store. Task records and
  tenant-scoped idempotency mappings are retained in a dedicated Redis service;
  raw idempotency keys are SHA-256 hashed before becoming Redis keys.
- Task updates are conditional state transitions. A completion/failure write
  is accepted only from an active state, so cancellation cannot be overwritten
  by a late graph result from the same or another replica.
- Locally owned background tasks are truly cancelled with `asyncio.Task.cancel`.
  Runtime shutdown marks interrupted active work failed instead of silently
  leaving a stale `running` record.
- Task records and tenant indexes have bounded retention; Redis uses AOF in the
  Compose deployment. WeKnora continues to use a separate Redis trust domain.

## Phase 06B controls (distributed runtime coordination)

- Tenant rate limits use a shared Redis fixed window; tenant identifiers are
  SHA-256 hashed before entering rate-limit keys.
- SSE tickets are shared, TTL-bound and atomically consumed with Redis `GETDEL`,
  so replica routing does not weaken single-use semantics.
- Task events and cancellation controls use namespaced Redis Pub/Sub channels.
  Cancellation commits terminal state before sending control, so a lost message
  cannot permit a late completion to reverse the decision.
- Cross-instance tests use two clients and two application lifespans against a
  shared FakeRedis server, including an end-to-end cancel request on a replica
  that does not own the graph coroutine.

## Phase 07 controls (durable worker execution)

- Redis Streams consumer groups retain unacknowledged work. Workers acknowledge
  only terminal outcomes and use heartbeat leases plus `XAUTOCLAIM` to recover
  work from a stopped process.
- Persisted attempt counts bound transient graph failures. Unexpected consumer
  errors do not kill the worker loop or acknowledge the affected delivery.
- Graceful shutdown moves active work to `retrying`; user cancellation remains
  terminal and conditional task transitions reject late or duplicate results.
- Delivery is explicitly at least once. Any future external-write tool must
  implement an idempotency key before it can be considered production-safe.

## Phase 08 controls (identity, tenant database isolation, prompt injection)

- Production JWT verification uses asymmetric JWKS keys with a fixed algorithm
  allowlist and exact issuer/audience checks. Unknown key IDs refresh the cache
  once, enabling rotation without accepting algorithm confusion.
- Tenant IDs cannot contain the checkpoint delimiter. Worker envelopes must
  match the persisted task tenant and thread before graph execution.
- All LangGraph checkpoint tables force PostgreSQL RLS. Each saver operation
  sets a transaction-local tenant context; absent or different context cannot
  read or write another tenant's checkpoint rows.
- Retrieved text is bounded, serialized as untrusted JSONL, and stripped of
  common model-control token syntax. Injection-like evidence emits an event but
  never changes the static graph route or tool risk allowance.
- The answer model has no tool-calling capability. Strict output parsing and
  the citation gate restrict accepted claims to retrieved evidence identifiers.

## Phase 09 controls (observability data)

- Logs redact bearer values, URL token/key/secret values and sensitive
  structured fields. Raw tenant IDs are replaced by stable truncated hashes.
- Prometheus labels contain route templates and statuses, never task IDs, raw
  paths, prompts or tenant IDs, bounding both disclosure and cardinality.
- Trace context crosses the durable queue, but spans include identifiers and
  timing only; prompts, retrieved evidence and credentials are excluded.
- The metrics endpoint is explicitly an internal deployment surface and must be
  restricted by network or ingress policy.

## Known gaps before production
- Extend approval flow to tool level: L3 external-write tools currently
  require task-level opt-in flags, not per-call HITL interrupts.
- Per-tenant WeKnora API keys (Phase 2 uses one default KB/key per deployment).
- Add reconnect/backoff and operational alerts for Redis Pub/Sub listeners;
  correctness survives a lost cancellation message, but compute may not stop
  immediately until the current call returns.
