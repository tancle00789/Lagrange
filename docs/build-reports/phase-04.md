# Phase 04' Build Report — Literature Subsystem

Date: 2026-08-04

## Scope delivered

Phase 4' was re-sequenced ahead of the doc's original Phase 4/5 (see
`pk_agent_roadmap_resequenced` decision): literature fetching is one of the
two primary functions of this research copilot.

- **ToolRegistry formalization** (`harness/registry.py`, doc §13): full
  `ToolSpec` (version/description/risk/timeout/max_retries/requires_approval/
  idempotency_strategy/schemas), uniform `ToolResult` (call_id/status/
  error_code/duration_ms/retryable), per-call risk + approval gating, timeout
  enforcement, retries limited to transient errors. Removed the stale
  duplicate `PromptAssembler`.
- **Literature subsystem** (`literature/`): `LiteratureRecord` (metadata-only,
  abstract ≤800 chars), four real sources — arXiv (Atom XML via defusedxml,
  client-side 3s rate limit), GitHub repo search (optional token), CrossRef
  (Science-family metadata, polite-pool mailto), Springer Nature (registered
  only when an API key is configured). Shared `SourceHttpClient` maps all
  failures to typed errors, retries only connection errors/5xx.
- **Ingest pipeline** (`literature/ingest.py`): SSRF-validated PDF download
  (https only, host allowlist, manual redirect following with per-hop
  revalidation, 25MB cap, content-type check) → WeKnora ingest. Runs only on
  explicit `ingest_external=true` (ADR 006 copyright boundary), ≤2 PDFs/task.
- **Graph branch**: `retrieve_internal → decide_external → retrieve_external
  → ingest_external → generate_grounded_answer → citation_gate → finalize`.
  External branch fires when `allow_external=true` and internal hits < 3.
  Dedupe by DOI/URL, cap 8 sources. Citation gate now accepts both internal
  chunk_ids and external source_ids; synthesis fallback covers external-only
  evidence. New SSE events: `external_retrieved` / `external_degraded` /
  `external_ingested`.
- **API/frontend**: `POST /api/v1/literature/search` (direct source fan-out,
  no ingest); task create/status carry `allow_external` / `ingest_external` /
  `external_sources`; SPA has the two opt-in checkboxes and an External
  Sources panel (title link, authors, venue/year, abstract, ingested badge);
  report citation validity covers source_ids.

## Verification (all real, this machine)

| Gate | Result |
|---|---|
| External API reachability from app container | arxiv 200, github 200, crossref 200, springer 401 (endpoint reachable, needs key) |
| `ruff check src tests` | ok (defusedxml added for S314) |
| `mypy src` | ok, 45 files |
| `pytest` | 67 passed (registry gating/timeout/retry, all four sources via MockTransport, ingest SSRF/size/redirect cases, graph branch/dedupe/gate/degradation, API incl. literature endpoint + external task) |
| `npm run build` (frontend) | ok |
| `docker compose up -d --build app` | ok |
| `scripts/smoke_test.sh` | ok |
| `scripts/smoke_test_literature.sh` | ok — real arXiv preflight, task with `allow_external` fetched 8 real external sources (arxiv+crossref+github), grounded report, all citations valid |
| `scripts/smoke_test_weknora.sh` (real qwen3:4b + WeKnora) | ok — regression confirmed after graph changes |

## Not done / caveats

- Springer Nature source is untested against the real API (no key); it is
  unregistered by default. Its response mapping is covered by MockTransport
  tests only.
- The GitHub source searches repositories, not code/papers-with-code; stars
  and topics land in `extra`.
- External evidence enters prompts as metadata+abstract only; answer quality
  for external-only tasks depends on abstract informativeness.
- `literature_ingest` is L2 gated by the task flag, not by a per-call HITL
  interrupt; tool-level approval interrupts remain future work (threat model
  known gaps).
- arXiv rate limiting is per-process; concurrent app replicas would each
  throttle independently.
