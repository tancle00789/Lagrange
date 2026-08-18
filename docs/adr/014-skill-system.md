# ADR 014: Skill System — Distilled Candidates, Human-Gated Approval, Deterministic Matching

## Status

Accepted (2026-08-17, Phase 7 / build report phase-12)

## Context

The docx §14 describes a skill lifecycle: distill reusable procedures from
completed tasks, evaluate them offline, and only then let them influence
future runs. This closes the roadmap's Phase 7 and aligns the runtime with
the AgentOS skill concept.

Key tensions:

- The LLM is good at proposing reusable procedures from a finished task —
  but an LLM must never be able to publish behavior into future runs
  unilaterally (prompt-injection via a poisoned task record would otherwise
  become a persistent, trusted prompt section).
- Skill *matching* happens on every task; spending model budget or risking
  nondeterminism there is unacceptable.
- Evaluation must prove the injection → matching → answer plumbing works,
  without depending on a real LLM (tests and CI have no GPU model).

## Decision

1. **Lifecycle as a state machine, approval is human-only.**
   `candidate → testing → approved → deprecated` (any pre-approved state may
   also go straight to `deprecated`). Generation code (distillation,
   evaluation) can only ever create or move records between `candidate` and
   `testing`. The `candidate/testing → approved` transition exists solely in
   `POST /api/v1/skills/{id}/{version}/approve`, which records
   `approved_by = "{tenant}:{user}"` from the authenticated principal.
   Approving a version deprecates every older approved version of the same
   family in the same transaction (publish = supersede).
2. **Evaluation gates approval, deterministically.** `SkillEvaluator` replays
   each eval case through the *real* research graph with a scripted
   `_ReplayModel` (never a real LLM): the candidate is loaded as if approved,
   matching must fire on its trigger conditions, the injected procedures
   section must reach the answer prompt, and expected model blocks must
   complete. Pass rate is pure assertion counting. Approval requires
   `status == "testing"` *and* at least one passing case
   (`MIN_APPROVAL_PASS_RATE`). An optional LLM judge (capability `"judge"`)
   may attach notes but never changes pass/fail. Distillation also asks the
   model for 2–4 suggested eval cases; they are parsed defensively and
   pinned to the candidate (`expect_skill_matched`) so the human approver
   starts from a working eval suite instead of a blank page.
3. **Matching is deterministic and free.** `SkillRegistry` holds only the
   latest approved version per family, loaded explicitly from the store
   (no implicit registration path). The graph's `match_skills` node matches
   keyword/regex `TriggerCondition`s — no LLM call, no budget cost — and
   stores only `matched_skill_ids` in state (small-object rule, ADR 002);
   records are re-read from the in-memory registry at prompt-build time.
   Matched procedures enter the answer prompt as a *trusted* section
   (`format_approved_procedures`) — trusted because they passed human
   approval, and clearly separated from untrusted evidence.
4. **Registry freshness is explicit.** `approve`/`deprecate` endpoints call
   `registry.refresh()` inline after the store transition, then publish on a
   Redis control channel (`{prefix}:control:skills`) via
   `RedisSkillRefreshCoordinator` so every replica reloads — the same
   pub/sub pattern as task cancellation (ADR 009). Single-process runtimes
   use the no-op `SkillRefreshCoordinator`.
5. **Persistence mirrors the runtime store.** Postgres (`skill_versions`,
   `skill_eval_runs`, idempotent DDL, `PgSkillStore`) when `database_url` is
   postgres; `InMemorySkillStore` otherwise (dev/tests). Transitions use
   `SELECT ... FOR UPDATE` so concurrent approve/deprecate cannot fork the
   lifecycle.

## Consequences

- A poisoned task record can at most produce a `candidate`; it cannot reach
  the answer prompt of any future task without a human clicking approve
  after a passing deterministic evaluation.
- Skill matching adds zero model calls and microseconds of regex work to
  every task.
- The evaluator doubles as an end-to-end regression test of the skill
  plumbing: matching, injection, model-block planning, and answer parsing.
- Distillation reuses the chat model under capability `"distill"` (registered
  in the router like `"answer"`/`"plan"`); a stronger model can be swapped in
  without touching call sites.
- Local 4B models systematically violate output-format details (underscore
  skill ids, block names as tool names). The distill path therefore
  normalizes ids (`kv_cache_simulation` → `kv-cache-simulation`) and
  hard-rejects unknown tools, instead of trusting format instructions.
  Verified live against qwen3:4b in `scripts/smoke_test_skills.sh`.
