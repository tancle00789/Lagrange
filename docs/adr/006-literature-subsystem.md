# ADR 006 — Literature Subsystem (External Retrieval as Harness Tools)

Status: accepted (Phase 4')
Date: 2026-08-04

## Context

The system's primary job is research assistance: fetch literature from arXiv,
GitHub, Nature (Springer Nature API) and Science-family metadata (CrossRef),
ground answers on it, and optionally ingest PDFs into the private WeKnora
library. Phase 2 established `KnowledgeProvider` as the boundary for the
*internal* knowledge subsystem (ADR 004). External literature sources differ
fundamentally: they are third-party, unauthenticated or lightly authenticated,
rate-limited, return metadata (not chunks), and carry copyright constraints.

## Decision

1. **External retrieval is a Harness tool, not a KnowledgeProvider.**
   Literature sources implement `LiteratureSource` (`literature/base.py`) and
   are exposed to the graph only through the formalized `ToolRegistry`
   (`literature_search`, risk L1). `KnowledgeProvider` stays the single
   boundary for WeKnora; external sources never write to graph state except
   through the tool's small-record contract.

2. **Metadata-only by default (copyright boundary).**
   `LiteratureRecord` carries title/authors/abstract(≤800 chars)/urls/doi/
   venue/published — never full text. PDFs are downloaded and ingested into
   WeKnora **only** when the task explicitly sets `ingest_external=true`,
   via the `literature_ingest` tool (risk L2, ≤2 PDFs/task, ≤25MB each,
   content-type checked, SSRF-allowlisted hosts, redirects re-validated).

3. **Degradation policy for external sources.**
   External retrieval is an optional enhancement. A failing source is skipped
   and reported (`errors` list in the tool result, `external_degraded` SSE
   event); remaining sources still contribute. Unlike ADR 004's internal
   policy, auth errors on an external source do **not** fail the task —
   a misconfigured optional source must not block research. Total tool
   failure degrades to internal-only answering with `external_error` set.

4. **Graph branch, not loop.**
   `retrieve_internal → decide_external → retrieve_external → ingest_external
   → generate_grounded_answer`. The branch fires only when
   `allow_external=true` and internal hits < 3. At most one external round per
   task (`retrieval_rounds`); no iterative re-retrieval in Phase 4'.

5. **Citations cover both evidence kinds.**
   The citation gate accepts internal `chunk_id`s and external `source_id`s
   (`{source_type}:{external_id}`). Claims citing anything else are dropped
   and counted, same as before.

6. **ToolRegistry formalization (doc §13).**
   `ToolSpec` carries version/description/risk/timeout/max_retries/
   requires_approval/idempotency_strategy/input+output schema; `ToolResult`
   wraps every call with call_id/status/error_code/duration_ms/retryable.
   Retries apply only to `TransientToolError`/timeouts; handler errors are
   not retried. Risk gating and approval gating are enforced per call.

## Consequences

- New external source = new `LiteratureSource` + one registration line; the
  graph and gate need no changes.
- arXiv rate limit (3s) is enforced client-side in `ArxivSource`.
- Nature source is registered only when `PK_AGENT_SPRINGER_NATURE_API_KEY` is
  set; otherwise silently absent (not an error).
- Full-text ingestion is opt-in per task, keeping the default flow
  metadata-only and copyright-safe.
