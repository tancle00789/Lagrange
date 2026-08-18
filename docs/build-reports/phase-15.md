# Phase 15 Build Report — Multi-Replica Skill Refresh + Eval Case Editor

Date: 2026-08-18
ADR: `docs/adr/014-skill-system.md` (decision 4 updated)
Depends on: phase-12 (skill system), phase-13 (deployment verification), phase-14 (frontend panel)

## Scope

Close the last two Phase-7 leftovers: cross-replica skill-registry
invalidation, and an eval-case editor in the Skills panel so suggested cases
can be reviewed/edited before evaluation instead of being taken as-is.

## Delivered

### Backend
- `pk_agent/skills/refresh.py`:
  - `SkillRefreshCoordinator` — no-op base for single-process runtimes
    (dev/tests), same shape as the cancellation coordinator.
  - `RedisSkillRefreshCoordinator` — pub/sub on `{prefix}:control:skills`;
    `start(refresh)` spawns a listener task with a ready-Event handshake
    (pattern mirrors `RedisCancellationCoordinator`, ADR 009), `request()`
    publishes, `aclose()` cancels the listener cleanly.
- `agentos/main.py` lifespan: builds the Redis coordinator when the runtime
  store is Redis (no-op otherwise), starts it with a `refresh_skills_safe`
  wrapper (exceptions logged, listener never dies), closes it on shutdown.
- `api/v1/skills.py`: `approve`/`deprecate` now go through
  `_publish_registry_change()` — local `registry.refresh()` first (this
  replica is correct immediately), then `skill_refresh.request()` (every
  replica reloads; the local listener firing again is a harmless idempotent
  double refresh).

### Frontend
- `api.ts`: `evaluateSkill(skillId, version, cases?)` — explicit cases
  override the backend's suggested-case fallback.
- `SkillsPanel`: per-skill collapsible "eval cases (N)" editor for
  candidate/testing records — seeded from `suggested_eval_cases` on first
  open; add (≤20) / remove / edit query + needles (one per line); Evaluate
  posts the edited draft and rejects an all-empty draft client-side.
- styles: `.case-editor` / `.case-row`, `.actions` vertically centered.

### Tests
- `test_coordination.py::test_redis_skill_refresh_reaches_other_replicas` —
  two fakeredis-backed coordinators, one publish wakes both listeners.
- `test_skills_api.py::test_approve_and_deprecate_notify_other_replicas` —
  recorder coordinator on app state asserts exactly one `request()` per
  approve/deprecate.

## Verification

- `pytest`: 163 passed, 1 skipped (torch local) — full suite green.
- `ruff` / `mypy` (73 files): clean.
- `tsc -b && vite build`: clean.
- Real-stack re-verification (`PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b`,
  full compose incl. executor): `scripts/smoke_test_skills.sh` **EXIT 0** —
  distill (real qwen3:4b) → evaluate → approve → new task SSE carries
  `skills_matched` with the approved skill id. (First two attempts failed
  environmentally: executor container and host Ollama were down after a
  Docker Desktop restart; not code issues.)

## Known limits

- Cross-replica refresh is verified by unit test (fakeredis) and by the
  single-replica smoke; a true two-replica compose profile remains untested
  (single-replica deployment today).
