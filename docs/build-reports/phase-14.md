# Phase 14 Build Report — Frontend Skills Panel + Suggested-Case Persistence

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md`
Depends on: phase-12 (skill system), phase-13 (deployment verification)

## Scope

Make the skill lifecycle usable day-to-day: a real UI instead of curl, and
persistence for distill-suggested eval cases so "one-click evaluate" works
long after the distill response is gone.

## Delivered

### Backend
- `SkillRecord.suggested_eval_cases` (≤5, persisted in the JSONB payload —
  backward compatible, old records default to empty). Distillation embeds
  the cases directly into the candidate record.
- `POST .../evaluate` body `cases` is now optional: empty falls back to the
  record's suggested cases (422 if neither exists). Explicit cases still
  override — the smoke script's constructed case is unaffected.

### Frontend (`frontend/src`)
- **Skills tab** (App-level Tasks/Skills tabs): `SkillsPanel` with three
  sections — Approved / Testing (awaiting approval) / Candidates.
  - Candidate → "Evaluate (N cases)" runs the suggested cases and shows
    pass/fail per case with failure reasons inline.
  - Testing → pass-rate badge; "Approve" enabled only after a passing
    evaluation (mirrors the backend gate); re-evaluate possible.
  - Any non-deprecated record → "Deprecate".
  - Status badges reuse the task-status palette (approved=green,
    testing=amber, candidate=blue, deprecated=red).
- **Task detail**: completed tasks get a "Distill skill" button; the result
  note points to the Skills tab (or reports "not generalizable").
- SSE timeline subscribes to the new `skills_matched` event type.
- api.ts/types.ts: full skill API surface + types.

### Hygiene
- `npm audit fix`: nanoid high-severity advisory → 0 vulnerabilities.
- `tsc -b && vite build` clean.

## Verification

- `pytest`: 161 passed, 1 skipped (torch local) — full suite green.
- `ruff` / `mypy`: clean.
- `scripts/smoke_test_skills.sh` against the rebuilt compose stack:
  **EXIT 0** — distill now succeeds on the first attempt (prompt
  clarification about block names vs tool names held), and the deprecated
  record in Postgres carries 3 persisted suggested eval cases.
- UI served from the app container at `/` (static build baked into image).

## Known limits

- The panel evaluates with suggested cases only; a case editor UI is future
  work (the API already accepts explicit cases).
- Multi-replica registry refresh is still unwired (single-replica refresh
  on approve/deprecate happens inline).
