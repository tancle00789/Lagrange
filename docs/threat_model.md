# Threat Model — Phase 4'

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

## Known gaps before production
- Replace dev bearer format with signed JWT/JWKS validation.
- Replace `?token=` SSE auth with short-lived single-use tickets.
- Add PostgreSQL RLS or schema-per-tenant isolation.
- Add Redis-backed distributed rate limiting.
- Add secret scanning and log redaction.
- Extend approval flow to tool level: L3 external-write tools currently
  require task-level opt-in flags, not per-call HITL interrupts.
- Add prompt-injection content isolation for retrieved evidence.
- Per-tenant WeKnora API keys (Phase 2 uses one default KB/key per deployment).

