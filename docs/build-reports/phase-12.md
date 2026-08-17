# Phase 12 Build Report — Skill System (Phase 7)

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md`

## Scope

Roadmap Phase 7 (docx §14): distill reusable procedures ("skills") from
completed tasks, evaluate them offline and deterministically, and let only
human-approved skills influence future runs — closing the last major
roadmap item and aligning with the AgentOS skill concept.

## Delivered

### skills module (`src/pk_agent/skills/`)
- `models.py` — `SkillRecord` (semver versioned, `extra="forbid"`, validated
  skill_id/procedure/regexes), `TriggerCondition`, `EvalCase`, `EvalResult`,
  `SkillCandidate`; `ALLOWED_TRANSITIONS` state machine
  (candidate → testing → approved → deprecated).
- `store.py` — `SkillStore` protocol; `PgSkillStore` (idempotent DDL,
  `skill_versions` + `skill_eval_runs`, `SELECT ... FOR UPDATE` transitions,
  approve supersedes older approved versions in one transaction) and
  `InMemorySkillStore` for dev/tests.
- `registry.py` — runtime view over latest-approved-per-family; explicit
  load/refresh, deterministic keyword/regex matching with priority ordering
  and a match cap; no LLM, no budget cost.
- `distill.py` — LLM distillation of a completed task into a candidate
  record (capability `"distill"`); unknown-tool and schema-validation gates;
  `next_version` minor bump; untrusted task record sanitized before entering
  the prompt.
- `evaluate.py` — `SkillEvaluator` replays eval cases through the real
  research graph with a scripted `_ReplayModel` (never a real LLM);
  deterministic pass/fail on matching, injected-procedure echo, and model
  block completion; regression detection against the baseline version;
  optional LLM judge attaches notes only.
- `prompts.py` — distill prompt builder and `format_approved_procedures`
  trusted prompt section.

### Graph integration
- New `match_skills` node (normalize → match_skills → retrieve_internal):
  deterministic match, publishes `skills_matched`, stores only
  `matched_skill_ids` in state (small-object rule).
- `generate_grounded_answer` injects the approved-procedures section via
  `PromptAssembler.build_answer_prompt(..., procedures_section=...)`.
- `build_research_graph(..., skill_registry=None)` — fully optional; graphs
  without a registry behave exactly as before.
- Research graph now shares `harness/parse.py` JSON/noise helpers (deduped
  from the model-block phase).

### API (`/api/v1/skills`)
- `GET ""` — latest approved per family (registry view).
- `GET /pipeline?status=...` — candidate/testing/deprecated listing.
- `GET /{skill_id}` — version family.
- `POST /distill {task_id}` — tenant-checked, completed-tasks-only; result
  is ALWAYS a candidate (422 on model producing invalid/unknown-tool output).
- `POST /{id}/{version}/evaluate {cases}` — deterministic replay; moves
  candidate → testing with pass rate; stores the eval run.
- `POST /{id}/{version}/approve` — human gate; requires testing status and
  ≥1 passing case; records `approved_by` from the JWT principal; refreshes
  the runtime registry inline.
- `POST /{id}/{version}/deprecate` — retires a version; refreshes registry.

### Runtime wiring
- Lifespan builds `PgSkillStore` (postgres URL) or `InMemorySkillStore`,
  loads approved skills into `SkillRegistry`, passes it to the graph, and
  closes the store on shutdown.
- `"distill"` and `"judge"` capabilities registered on the chat model.

### Cleanup
- Removed the dead placeholder `SkillRegistry` dataclass from
  `harness/registry.py` (superseded by the real registry).

## Verification

- `pytest`: 160 passed, 1 skipped (torch block, local `importorskip`) —
  includes 23 new tests: skills models/store/registry/distill/evaluate,
  graph skill injection, and full API lifecycle (auth, approve gating,
  evaluate→approve→deprecate, distill tenant/completion checks).
- `ruff check src tests`: clean.
- `mypy src`: clean (72 files).

## Known limits / future work

- Multi-replica registry refresh (Redis pub/sub, like task cancellation) is
  not wired; a second replica would need a restart or manual refresh to see
  newly approved skills.
- The frontend has no Skills panel yet; the lifecycle is API-driven.
- The LLM judge is registered but its notes are advisory-only by design.
