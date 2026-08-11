# Phase 11 Build Report — Model-Block System (Phase 5′)

Date: 2026-08-10
Plan: `optimized-twirling-breeze.md` → "Phase 05′ 模型块系统实施计划"
ADR: `docs/adr/013-model-block-system.md`

## Scope

User's second core function: the agent autonomously decides (via the LLM)
whether to run pre-registered model blocks — classical ML, deep learning, or
numerical simulation — to support research, with results and artifacts
surfaced in the answer and UI.

## Delivered

### modelblocks module (`src/pk_agent/modelblocks/`)
- `models.py` — `ModelBlockSpec` / `ModelRunRequest` / `ModelRunResult`.
- `base.py` — `ModelBlock` protocol, `RunContext` (tenant/task/run-scoped
  artifact keys).
- `registry.py` — explicit-registration catalog with per-block Pydantic
  param validation (`extra="forbid"`), schema-rich catalog for API + a
  compact JSON summary for the plan prompt.
- `artifacts.py` — `ArtifactStore` protocol; `MinioArtifactStore` (minio SDK
  via `asyncio.to_thread`, lazy import) and `LocalArtifactStore` (path
  traversal-safe). 
- `sklearn_tabular.py` — logistic/RF classification & regression, inline JSON
  rows (≤2000×51) or synthetic demo; holdout metrics + joblib artifact.
- `torch_mlp.py` — configurable MLP, CUDA-if-available with 50% memory cap,
  loss-curve JSON + weights artifacts.
- `kv_cache_sim.py` — dependency-free KV-cache memory/quality trade-off
  simulation across compression ratios.
- `broker.py` — `ModelRunBroker` (XADD submit; pubsub + 1s-poll await with
  deadline) and executor-side `run_model_worker` (consumer group, per-block
  runtime cap via `wait_for`, result hash + publish). Empty reads sleep
  `block_ms` so non-blocking fakes cannot busy-spin.
- `tools.py` — `model_block_run` ToolSpec (L2, `max_retries=0`) with
  server-side schema re-validation; submit/timeout failures map to
  `TransientToolError`.

### Graph
- New nodes `plan_model_calls` (capability `"plan"`, strict-JSON plan,
  citation-gate-style validation, ≤`model_block_max_calls`) and
  `run_model_blocks` (sequential L2 tool calls, degrade-on-failure), wired
  between retrieval and answer generation.
- `generate_grounded_answer` receives completed runs as a trusted
  "Simulation results" prompt section; failed runs excluded from the prompt.
- State adds `model_calls` / `model_runs` / `model_error` (small objects
  only). Budget charges: plan +1, each executed run +1.

### Deployment
- `executor_main.py` + `pk-agent-executor` entry point; Dockerfile gains an
  `executor` target (uv `executor` group: numpy/scikit-learn/torch; PyPI
  torch wheel bundles CUDA runtime; `TORCH_EXTRA_INDEX` ARG fallback). The
  API image installs no heavy ML deps.
- Compose: `executor` service (GPU reservation via deploy.devices,
  `cpus: 4`, `mem_limit: 8g`) and `minio` service (via daocloud mirror) with
  health-gated startup; app/executor env wiring for MinIO and timeouts.
- Config: `model_blocks_*`, `model_run_result_ttl_seconds`, `minio_*`,
  `artifacts_local_dir`; `.env.example` updated.

### API + frontend
- `GET /api/v1/modelblocks` (catalog with param schemas) and
  `GET /api/v1/artifacts/{id}` (tenant-prefix authorization, traversal-safe,
  content-type by suffix).
- `TaskStatusResponse.model_runs`; executor results persisted to the task
  record.
- Frontend: Model Runs panel (block, status, metrics, artifact download via
  authenticated blob fetch), new SSE event types, `listModelBlocks` /
  `downloadArtifact` API helpers.

## Verification (this machine)

| Gate | Result |
|---|---|
| `uv sync --frozen` | OK (lock updated: minio, sklearn, scipy, torch + CUDA wheels) |
| `ruff check src tests` | OK |
| `mypy src` | OK (63 files) |
| `pytest` | **137 passed, 1 skipped** (torch block test — torch intentionally not in local dev env) |
| `npm run build` | OK (0 vulnerabilities previously verified) |
| `docker compose up -d --build` | OK — app + executor images built (executor stage ~513s, torch 2.13.0+cu13 from PyPI); full stack healthy |
| `scripts/smoke_test.sh` | OK — real WeKnora + qwen3:4b, grounded report |
| `scripts/smoke_test_modelblocks.sh` | OK — planner → `kv_cache_sim` → executor → MinIO artifact → API download |

### Deployment verification (2026-08-11, Docker Desktop WSL2)
- `docker logs <executor>`: three blocks registered, MinIO bucket
  `pk-agent-artifacts` created.
- GPU passthrough confirmed: `torch.cuda.is_available()=True` on
  **NVIDIA GeForce RTX 5070 Ti Laptop GPU**; a direct `torch_mlp` run through
  the Redis stream completed with `device_cuda: 1.0` and both artifacts
  (loss curve + weights) in MinIO.
- End-to-end smoke: preflight catalog listed all three blocks; task planned
  by qwen3:4b executed `kv_cache_sim` with metrics
  (`baseline_memory_gib`, `min_memory_gib`, `min_quality_proxy`, …) and the
  curve artifact downloaded via `GET /api/v1/artifacts/{id}`.
- MinIO host ports are **19000/19001** (Windows reserves 9000); container
  traffic still uses `minio:9000`.

New tests: block real-runs (sklearn demo/inline regression, kv_sim curve
math, params rejection), artifact store (roundtrip, traversal),
registry (duplicate/unknown/schema), tool handler (server-side re-validation,
transient mapping), broker (fakeredis roundtrip, invalid block, timeout),
graph (happy path, unknown block, bad params, max-calls cap, unparseable
plan, run failure degradation, no-capability/no-registry skips, defensive
parser), API (catalog, auth, artifact roundtrip/cross-tenant/traversal,
`model_runs` field).

### Notable debugging
- fakeredis ignores stream block timeouts and returns immediately: the
  executor loop busy-spun and starved the event loop, hanging the broker
  test. Fixed by sleeping `block_ms` on empty reads (correct on real Redis
  too).
- redis-py 5.x deprecates `PubSub.close()` but types-redis stubs lack
  `aclose()` — runtime-correct call with a targeted `type: ignore`.
- qwen3 is a thinking model: its `<think>…</think>` trace leaked into the
  plan JSON. Fixed by sending `"think": false` to Ollama plus defensive
  stripping (`_strip_model_noise` / `_load_json_object`) in the graph.
- Live run exposed that qwen3:4b proposed `compression_ratios=[0.1, …]`
  (inverted semantics). Range validation moved from `run()` into a
  `KvCacheSimParams` field validator so the plan gate rejects it, and the
  block description now spells out "ratio r means memory is divided by r".
- docker.io is TLS-blocked on this network — base images pulled via the
  `docker.m.daocloud.io` mirror and retagged.
- Host port 9000 is in a Windows reserved range (Hyper-V exclusion) — MinIO
  host ports moved to 19000/19001.

## Deployment sign-off — completed 2026-08-11
1. Docker Desktop (WSL2 + GPU) started. ✅
2. `docker compose up -d --build` — executor image built with CUDA torch
   from PyPI (no custom index needed). ✅
3. Executor logs show the three registered blocks and MinIO bucket
   creation; GPU verified via live `torch_mlp` run (`device_cuda: 1.0`). ✅
4. With `PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b`:
   `bash scripts/smoke_test_modelblocks.sh http://localhost:8000` → EXIT 0
   (planned run completed, metrics present, artifact downloadable). ✅

## Known limitations
- In-memory runtime store mode (tests) has no Redis, so `model_block_run` is
  not registered; the graph plans but degrades at execution. Production mode
  (`runtime_store=redis`) wires the broker.
- Executor crash mid-run leaves the stream entry pending in the consumer
  group (no lease/autoclaim yet); the API-side await times out and the run is
  reported failed. Lease takeover can be added mirroring ADR 010 if needed.
- Artifact endpoint streams whole files into memory — fine for current
  block outputs (≤ a few MB), revisit if blocks start emitting large files.
