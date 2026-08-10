# Production Readiness Checklist

## Proven in automated tests

- [x] Tenant-scoped API reads, lists, idempotency and terminal transitions.
- [x] Redis shared rate limits, SSE tickets, events and cancellation.
- [x] Stream ACK, retry, shutdown recovery, crash lease takeover and bounded
  task attempts.
- [x] JWKS algorithm/claim checks and unknown-key rotation refresh.
- [x] Checkpoint tenant wrapper rejects cross-tenant configs before SQL.
- [x] Prompt evidence framing, token neutralization and citation gating.
- [x] Credential-redacted correlated logs, route-safe metrics and trace carrier.
- [x] Browser token/task/report flow in Chromium.

## Required per deployment

- [ ] Run `scripts/verify_core_deployment.sh` against real containers.
- [ ] Confirm three forced RLS policies and zero unscoped checkpoint rows.
- [ ] Exercise IdP staging keys, issuer/audience and a full rotation overlap.
- [ ] Restrict `/metrics` and PostgreSQL/Redis to internal networks.
- [ ] Configure alert routing and an OTLP backend; validate one test alert/trace.
- [ ] Inject secrets through the platform secret store and run repository/image
  secret and vulnerability scans.
- [ ] Establish backup/restore objectives for PostgreSQL, Redis AOF and WeKnora.
- [ ] Load-test worker concurrency against model and provider capacity.
- [ ] Confirm external-write tools use idempotency keys before enabling them.

## Release evidence to retain

- Git commit and dependency lock hashes.
- Ruff, mypy, pytest, frontend build and Playwright outputs.
- Container image digests and vulnerability scan results.
- Deployment verifier output, RLS policy query and Redis `XINFO`/`XPENDING`.
- IdP rotation test, backup restore test and alert delivery evidence.

