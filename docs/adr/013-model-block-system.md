# ADR 013: Model-Block System — LLM-Planned, Schema-Gated, Executor-Run

## Status

Accepted (2026-08-10, Phase 5′ / build report phase-11)

## Context

The agent's second core function (per the user's product definition): given a
natural-language instruction, run pre-registered model blocks (classical ML,
deep learning, numerical simulations) to support research. The source docx
has no model-block concept; the applicable constraints are the tool system
(§13), object storage for large artifacts (§8), small-object Graph State (§7),
and the sandbox requirements (§18).

Key tensions:

- The user wants the LLM to decide *whether and which* block to run — but an
  LLM must never be able to inject arbitrary code or unbounded workloads.
- Blocks need sklearn/torch (heavy deps, GPU) which must not bloat or slow the
  API image.
- Trained models and curves are large binary artifacts that must not enter
  Graph State or the checkpoint database.

## Decision

1. **LLM-autonomous planning behind a schema gate.** A `plan_model_calls`
   graph node (capability `"plan"`) asks the LLM for strict JSON
   `{model_calls: [{block_name, params}]}`. The node validates every call
   like a citation gate: block must be registered in `ModelBlockRegistry`,
   params must pass that block's Pydantic schema (`extra="forbid"`), and at
   most `model_block_max_calls` (default 2) calls survive. Invalid calls are
   dropped and recorded in `model_error`. The plan prompt carries the catalog
   (names, descriptions, param types) but not user-controlled code.
2. **Execution via a ToolRegistry L2 tool in a dedicated executor container.**
   Validated calls go through the tool `model_block_run` (risk L2, no
   auto-retry). The API-side handler re-validates the block schema (the
   graph's plan is not trusted), XADDs a `ModelRunRequest` to the Redis
   stream `{prefix}:modelruns`, and awaits the result (pubsub + 1s polling
   fallback) up to `model_block_timeout_seconds`. The executor service
   (same codebase, `pk-agent-executor` entry, consumer group on the stream)
   runs the block, uploads artifacts, and publishes `ModelRunResult`.
   - **Why a persistent executor rather than §18-style per-task sandboxes:**
     §18 targets *arbitrary untrusted code*. Blocks are pre-registered,
     trusted, reviewed code with strictly validated params — there is no code
     injection surface. A persistent executor with container-level resource
     limits (`cpus`, `mem_limit`, GPU reservation) gives the same blast-radius
     control without per-run container startup cost. This is a deliberate,
     documented relaxation; any future *user-supplied code* feature must use
     the full §18 sandbox instead.
3. **MinIO for artifacts, ids only in State.** `ArtifactStore` protocol with
   `MinioArtifactStore` (minio SDK in `asyncio.to_thread`) and
   `LocalArtifactStore` (dev/tests). Artifact keys are
   `{tenant_id}/{task_id}/{run_id}-{filename}` — the tenant prefix is the
   authorization boundary enforced by `GET /api/v1/artifacts/{id}`. Graph
   State stores only `run_id`, block name, small metrics dict, and artifact
   ids. The answer prompt receives completed runs' metrics in a trusted
   "Simulation results" section; failed runs are excluded from the prompt but
   kept in state for the UI.
4. **Degradation over failure.** Planning is skipped (empty calls) when no
   planner model is registered, the registry is absent, or the LLM output is
   unparseable. A failed run records `model_error` and the task continues to
   answer generation (same spirit as ADR 006 external-source degradation).
5. **Heavy deps isolated to the executor image.** sklearn/torch live in a uv
   `executor` dependency group installed only in the Dockerfile `executor`
   target (and dev for tests). The API image stays slim. The PyPI torch wheel
   bundles the CUDA runtime, so GPU passthrough works without a custom index;
   `TORCH_EXTRA_INDEX` remains as a build ARG fallback. The torch block caps
   CUDA memory at 50% (`set_per_process_memory_fraction`) so a run cannot
   starve the host GPU.

## First three blocks

- `sklearn_tabular` — logistic/RF classification & regression on inline JSON
  rows (≤2000×51) or synthetic demo data; joblib model artifact.
- `torch_mlp` — configurable MLP (layers/epochs/lr/batch), CUDA if available,
  loss-curve JSON + weights artifacts.
- `kv_cache_sim` — dependency-free KV-cache memory/quality trade-off curves
  across compression ratios (supports the user's KV-cache research).

## Consequences

- LLM can trigger GPU/CPU-heavy work: bounded by schema validation, max 2
  calls per task, per-block runtime caps, tool-level timeout, and container
  resource limits. Budget accounting charges plan (+1) and each run (+1)
  against `model_budget_max_calls`.
- A timed-out run is never auto-retried (`max_retries=0`) because executor
  side effects (artifacts) are not idempotent; the Redis stream retains the
  request for operator inspection.
- fakeredis ignores stream block timeouts: the worker sleeps `block_ms` on
  empty reads so tests (and any non-blocking client) cannot busy-spin.
- Compose adds `executor` (GPU reservation, 4 CPU / 8 GiB caps) and `minio`
  services; deployment verification requires Docker Desktop with WSL2 GPU
  support.
