# ADR 011 — JWKS Authentication, Checkpoint RLS and Untrusted Evidence

Status: accepted (Phase 08)
Date: 2026-08-05

## Context

The production boundary still depended on a deployment-shared HS256 secret,
checkpoint tenant separation was enforced only by application identifiers, and
retrieved text entered the answer prompt as prose. Those controls were useful
for development but did not provide key rotation, database-enforced isolation,
or an explicit boundary against indirect prompt injection.

## Decision

- Production requires an HTTPS JWKS URL plus exact issuer and audience values.
  Only RS256 and ES256 signing keys are accepted. Tokens require `kid`, `exp`,
  `iat`, `sub`, `tenant_id`, `iss` and `aud`; an unknown `kid` triggers one
  immediate JWKS refresh to support rotation. Development may retain HS256 and
  explicit `dev:` tokens.
- Tenant identifiers use a restricted character set. Queue execution checks the
  envelope tenant and thread against the durable task record before invoking
  LangGraph.
- PostgreSQL checkpoint tables enable and force row-level security. A wrapper
  validates `{tenant_id}:{task_id}` thread IDs, serializes operations on the
  saver connection, and sets `pk_agent.tenant_id` transaction-locally before
  every read/write/delete. RLS compares that value with the thread tenant
  prefix. Returned checkpoint configs retain the tenant context.
- Internal snippets and external metadata are serialized as bounded JSONL in a
  clearly labelled untrusted evidence section. Known model-control token syntax
  is neutralized and instruction-like evidence emits a detection event.
- Prompt hardening is defense in depth: the answer model cannot choose or call
  tools, output is parsed as data, and the citation gate accepts only IDs that
  were actually retrieved.

## Consequences

- Identity-provider key rotation no longer requires redeploying a shared
  symmetric secret. JWKS availability is required only on cache miss/expiry.
- A missing tenant database context sees no checkpoint rows, including for the
  table owner because RLS is forced. Cross-tenant config mismatches fail before
  SQL execution.
- Prompt-injection detection is intentionally a signal, not a claim that text
  classification is perfect. Suspicious evidence may still be used as factual
  data, but cannot alter tool routing or bypass citation validation.
- Real identity-provider and PostgreSQL policy behavior must still be exercised
  during the final deployed-environment verification.

