# Phase 02 Build Report

## Delivered
- WeKnora contained as a knowledge subsystem in `docker-compose.yml`:
  `weknora-app` + `weknora-docreader` + `weknora-postgres` (ParadeDB) +
  `weknora-redis` on isolated `weknora-net`; `app` is the only bridge.
  WeKnora repo is build-referenced read-only, never modified.
- Provider contract layer: `providers/contract.py` (KnowledgeHit /
  SourceReference / KnowledgeChunk / KnowledgeIngestionJob / CitationLocation),
  `providers/errors.py` (Unavailable / Timeout / Auth / BadRequest / NotFound).
- Thin transport `providers/weknora_client.py`: `X-API-Key` auth, tenacity
  bounded retries (3 attempts, <=8s backoff, 5xx/connection only), typed error
  mapping, no secret logging.
- Real `WeKnoraKnowledgeProvider`: raw JSON -> contract models, snippet
  truncation (500 chars) before anything reaches graph state, tenant -> KB
  mapping with no cross-tenant fallback.
- `FakeKnowledgeProvider` for tests; same Protocol, covered by the same
  contract tests.
- LangGraph: `START -> init -> retrieve_knowledge -> echo -> finalize -> END`.
  Retrieval outage degrades to empty hits + `knowledge_error` (ADR 004);
  auth errors fail loudly. State carries only chunk_id/score/source/snippet.
- Bootstrap `scripts/init_weknora.py`: health wait, register/login, scoped API
  key, embedding model, KB — idempotent, secrets only on stdout.
- Real integration smoke `scripts/smoke_test_weknora.sh` (opt-in, not CI).
- Docs: ADR 004, `docs/runbooks/weknora-ops.md`, threat model Phase 2 section.

## Verification
```bash
uv sync --frozen          # ok (tenacity added to lock)
ruff check src tests      # ok
mypy src                  # ok, 28 files
pytest                    # 20 passed
docker compose up -d      # 6 services up, weknora-app healthy
bash scripts/smoke_test.sh http://localhost:8000          # smoke ok
python scripts/init_weknora.py                            # tenant/key/model/KB created
bash scripts/smoke_test_weknora.sh http://localhost:8000  # real smoke ok
```

Real smoke evidence: uploaded `smoke-quixotic-zephyr-*` document through the
WeKnora API (knowledge id `1b3cd041-...`), parsing completed with host Ollama
`qwen3-embedding:0.6b`, pk_agent task `75dca332...` completed with the marker
flowing from a real WeKnora chunk through the graph into the model context.

## Notable deviations / fixes during build
- `weknora-docreader` reuses the local published image (~4GB source build is
  impractical per-run); `build:` retained for reproducibility.
- weknora-app entrypoint chowns `/app/skills/preloaded`, so the skills bind
  mount must be read-write (matches upstream compose).
- WeKnora SSRF validation rejects `host.docker.internal` by default; compose
  sets `SSRF_WHITELIST_EXTRA=host.docker.internal` for the dev Ollama path.
- Windows git-bash curl mangles `/tmp` multipart paths with `;filename=`
  modifiers; smoke script uses a cwd-relative temp file.

## Known gaps (tracked in threat model)
- Single default KB/key per deployment; per-tenant WeKnora keys are future work.
- No RLS on checkpoint DB; no backup automation for WeKnora volumes.
