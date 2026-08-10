# Phase 08 Build Report — Production Security Isolation

Date: 2026-08-05

## Scope delivered

- Added asynchronous JWKS verification with RS256/ES256 allowlisting, exact
  issuer/audience validation, required temporal/identity claims, TTL caching and
  key-rotation refresh.
- Made JWKS configuration mandatory in production while retaining explicit
  development-only authentication paths.
- Added PostgreSQL forced RLS policies for all LangGraph checkpoint data tables
  and a transaction-scoped tenant saver wrapper.
- Added queue-envelope tenant integrity checks and restricted tenant identifier
  syntax so checkpoint prefixes cannot be forged with delimiters.
- Added untrusted JSONL evidence framing, model-control-token neutralization,
  prompt-injection signals and tests. Static graph routing and citation gating
  remain the primary capability/output boundaries.
- Added ADR 011 and an identity/tenant-isolation operations runbook.

## Verification

| Gate | Result |
|---|---|
| `uv lock` | ok, 80 packages |
| `ruff check --no-cache src tests` | ok |
| `mypy src` | ok, 50 source files |
| Focused security/isolation tests | 15 passed |
| Full pytest outside restricted WSL sandbox | 100 passed |

## Explicit boundary

Unit tests verify the RLS wrapper, tenant rejection, asymmetric signatures and
rotation with a mocked JWKS endpoint. A real IdP is not required for the
portfolio deployment, but real PostgreSQL RLS and container checks remain
mandatory Phase 10 evidence. Prompt injection is reduced through capability
separation and output validation; no text-only filter is represented as a
complete solution.

