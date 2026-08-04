# Phase 03 Build Report — Minimal Knowledge Workflow + Web UI

Date: 2026-08-04

## Scope delivered

- Research graph replacing the echo graph:
  `normalize → retrieve_internal → generate_grounded_answer → citation_gate → finalize`
  (`src/pk_agent/graph/research_graph.py`). Echo graph deleted.
- Async model layer: `Model.generate` is now async; `ModelRouter` routes by
  capability (`"answer"`). New `OllamaModel` (`harness/ollama.py`) calls host
  Ollama `/api/chat` with timeout + single transport retry. ADR 005.
- `PromptAssembler.build_answer_prompt` instructs strict JSON
  `{answer, claims:[{text, chunk_ids}]}`; `_parse_model_output` strips code
  fences and degrades to plain text on parse failure.
- `citation_gate` drops chunk_ids not present in `knowledge_hits`
  (counted in `dropped_citations`), synthesizes one claim from the top hit
  when the model produced none, and marks `grounded=False` when no evidence
  was retrieved.
- Domain models (`domain/research.py`): Evidence / Claim / Report. Graph
  state carries only small objects (≤500-char snippets, ≤300-char claims).
- API: `GET /api/v1/tasks` (tenant-scoped, newest-first, limit 100),
  `report` on `TaskStatusResponse`, CORS for the Vite dev origin, static SPA
  hosting from `/app/static` (or `frontend/dist` in dev).
- SSE auth: `?token=` query fallback added to `get_current_principal`
  (EventSource cannot set headers; threat model notes the dev-only trade-off).
- Frontend (`frontend/`, React 18 + Vite 5 + TS, dark theme, no component
  library): TokenGate, TaskForm (with require_approval), TaskList (5s poll),
  TaskDetail (SSE event timeline, knowledge hits, report with per-claim
  citation validity, approve/reject via resume, cancel). Multi-stage
  Dockerfile builds the SPA into the app image; no extra compose service.

## Verification (all real, this machine)

| Gate | Result |
|---|---|
| `uv sync --frozen` | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok, 32 files |
| `pytest` | 28 passed (research graph happy/paused/parse-degradation/gate branches, provider contract, retrieval degradation, API incl. list + approve flow) |
| `npm run build` (frontend) | ok (tsc + vite) |
| `docker compose up -d --build app` | ok; SPA served at `http://localhost:8000/` |
| `scripts/smoke_test.sh` | ok |
| Real LLM: `PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b` + `scripts/smoke_test_weknora.sh` | ok — real chunk in `knowledge_hits`, grounded report; answer produced by qwen3:4b with 1 verified claim, 0 dropped citations |
| SSE `?token=` auth | verified via curl (`snapshot` event received) |

Note on ollama: `registry.ollama.ai` was unreachable (TLS timeout), so
qwen3:4b was pulled via ModelScope (`modelscope.cn/unsloth/Qwen3-4B-GGUF:Q4_K_M`)
and tagged locally as `qwen3:4b`. Docker base images similarly came via the
DaoCloud mirror.

## Incidents found and fixed during this phase

1. **Phase 2 smoke was a false positive.** It asserted the marker in
   `result`, but the echo graph echoed the query (which contained the marker),
   so it passed while retrieval was silently broken. The Phase 3 assertion
   checks `knowledge_hits` and `report.grounded` — the smoke now proves real
   retrieval.
2. **WeKnora retrieval was silently dead since Phase 2.** `RETRIEVE_DRIVER`
   was unset on weknora-app, so no retrieval engine registered and
   `hybrid-search` always returned `{"data":null}` with HTTP 200. Fixed in
   compose (`RETRIEVE_DRIVER=postgres`).
3. **Skipped conditional migrations.** WeKnora's `000002_embeddings` (and the
   `000007` tag_id column) are gated on `app.skip_embedding`, which was true
   at first boot; golang-migrate still recorded them as applied. Repaired by
   applying the idempotent SQL manually; documented in
   `docs/runbooks/weknora-ops.md`.

## Not done / caveats

- Browser click-through of the SPA was verified at the HTTP level (SPA
  served, API + SSE + auth working); visual interaction should be sanity
  checked once at `http://localhost:8000/`.
- qwen3:4b on CPU takes ~10–60s per answer; the smoke poll budget is 30s and
  may need a retry on a cold model.
- `?token=` SSE auth is dev-only (see threat model Phase 3 section).
