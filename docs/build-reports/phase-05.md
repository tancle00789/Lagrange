# Phase 05 Build Report — Production Authentication Boundary

Date: 2026-08-05

## Scope delivered

- Production runtime validation rejects the default/short JWT secret and
  disables `dev:` bearer tokens. Signed JWTs now require `exp`, `sub` and
  `tenant_id`, with explicit type validation for identity and roles claims.
- Replaced the browser's long-lived `?token=<bearer>` EventSource URL with an
  authenticated SSE-ticket exchange. Tickets are cryptographically random,
  task- and tenant-bound, expire after 60 seconds by default and are single-use.
- Tenant-scoped task idempotency keys close a cross-tenant task-ID disclosure;
  oversized keys are rejected. Missing roles now return HTTP 403.
- Frontend transparently requests an SSE ticket before opening EventSource and
  reports ticket-exchange failure on the task event timeline.
- Upgraded Vite 5 → 8.2 and the React plugin 4 → 6 after a clean install found
  one high and one moderate development-server advisory. The clean upgraded
  dependency tree reports zero known vulnerabilities; Docker now pins the
  required Node 20.19 baseline.
- Added ADR 007 and updated the threat model/configuration example.

## Verification

| Gate | Result |
|---|---|
| `ruff format --check` (10 changed Python files) | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok, 46 source files |
| `tests/unit/test_security.py` | 4 passed |
| Direct async API security smoke | ok — 403 RBAC, tenant-scoped idempotency, cross-tenant ticket denial and single-use replay denial |
| `npm run build` (Node 20.19.4, clean locked Vite 8 install) | ok |
| `npm audit` after upgrade | ok, 0 vulnerabilities |
| Full `pytest` in the temporary WSL verifier | originally blocked by managed-sandbox cross-thread asyncio wakeups; resolved during Phase 06 diagnosis. The expanded 80-test suite now passes outside the restricted sandbox. |

The prior Phase 4' baseline was 67 passing tests; this phase added seven logical
test cases. See `docs/runbooks/wsl-test-hangs.md` for the resolved verifier issue.

## Deferred

- Asymmetric JWT/JWKS validation and rotation.
- Redis-backed distributed rate limiting and shared SSE/task state.
- PostgreSQL RLS or schema-per-tenant checkpoint isolation.
