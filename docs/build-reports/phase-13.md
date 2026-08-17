# Phase 13 Build Report — Skill System Deployment Verification + Distill Eval Cases

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md` (updated)
Smoke: `scripts/smoke_test_skills.sh`

## Scope

Close the "tested only in memory mode" gap from phase-12: verify the skill
subsystem against the real compose stack (Postgres + Redis + real qwen3:4b
LLM + executor), and make distillation generate suggested eval cases so the
approval loop starts from a working eval suite.

## Delivered

### Deployment verification (real stack, real LLM)
- `scripts/smoke_test_skills.sh` — full lifecycle against the compose stack:
  1. preflight skills endpoint;
  2. KV-cache source task (asserts a *completed* `kv_cache_sim` model run —
     fails fast with a hint when the stack runs FakeModel);
  3. distill via real qwen3:4b (up to 3 attempts, 422s surfaced and retried);
  4. deterministic offline evaluation (constructed case, needle = procedure
     prefix, so the gate doesn't depend on the 4B model quoting itself);
  5. approve (records `approved_by` from the principal);
  6. a NEW task matching the trigger must emit a `skills_matched` SSE event
     carrying the skill id (proves PgSkillStore → registry → graph injection
     on a fresh task);
  7. cleanup: deprecate, so reruns start clean.
- Result: **EXIT 0**. Postgres afterwards holds the full lifecycle record
  (`skill_versions`: approved_by `tenant-smoke:smoke-user`, deprecated by
  cleanup; `skill_eval_runs`: 1 run).

### Real-LLM findings that changed the code
- qwen3:4b reliably emits `kv_cache_simulation` (underscores) for skill_id
  regardless of format phrasing → `normalize_skill_id()` coerces to
  lowercase-dash instead of rejecting (verified live: rejection → acceptance).
- The model confuses model-block names with tool names
  (`required_tools: ["kv_cache_sim"]`) → prompt now states block names are
  not tools; the unknown-tool gate remains the hard backstop.
- App container needs `PK_AGENT_OLLAMA_CHAT_MODEL` set explicitly (no .env
  on this machine); otherwise distill instant-fails against FakeModel.

### Distill-generated eval cases
- Distill prompt requests 2–4 eval cases with verbatim procedure needles.
- `distill_candidate_record` now returns `(record, eval_cases)`: cases are
  parsed defensively (malformed entries dropped), capped at 5, and every
  case's `expect_skill_matched` is pinned to the actual candidate id —
  the model cannot suggest cases that "validate" a different skill.
- `POST /api/v1/skills/distill` response gains `eval_cases`, ready to feed
  into `POST .../evaluate` (and, later, the frontend's one-click flow).

## Verification

- `scripts/smoke_test_skills.sh`: EXIT 0 (see above).
- `pytest`: full suite green (distill unit tests cover normalization,
  eval-case parsing/pinning/dropping; API test covers the new response
  shape).
- `ruff` / `mypy`: clean.

## Notes

- The compose stack was left running (db/redis/minio/app/executor) with
  `PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b`.
- The first smoke attempt also exposed that `docker compose up --build`
  hung in a detached shell; foreground `up -d` works — operational note,
  not a code issue.
