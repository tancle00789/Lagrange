# Phase 06 Build Report — Durable Task Runtime

Date: 2026-08-05

## Scope delivered

- Added the `TaskStore` abstraction with lock-protected memory and Redis
  implementations. Every task API now reads/writes through this boundary.
- Redis persists small task/report records, tenant task indexes and hashed
  idempotency mappings with configurable retention. Production fails closed
  unless the Redis runtime store is selected and reachable at startup.
- Task writes are conditional transitions. Completion and failure can write
  only from active states, closing the cancel-vs-complete race.
- API processes retain handles for the LangGraph tasks they own. DELETE now
  calls `asyncio.Task.cancel`; shutdown also terminates owned work and marks
  interrupted records failed.
- Added a dedicated AOF Redis service and volume to Compose, separate from the
  WeKnora Redis subsystem.
- Added ADR 008, runtime configuration examples and memory/Redis/cancellation
  tests using `fakeredis`.

## Verification

| Gate | Result |
|---|---|
| `uv lock` + frozen sync in temporary Linux verifier | ok, 80 packages (including Starlette TestClient's `httpx2`) |
| `ruff format --check` (6 Phase 05/06 security/runtime files) | ok |
| `ruff check src tests` | ok |
| `mypy src` | ok, 47 source files |
| Runtime/security focused tests | 10 passed — memory store, Redis store, idempotency, conditional transitions, real coroutine cancellation, production config and SSE/JWT controls |
| Test collection | 80 tests collected |
| Compose YAML parse | ok |
| Full `pytest` outside the restricted WSL sandbox | ok, 80 passed |

The initial hang was isolated to the managed sandbox's inability to wake an
asyncio loop across threads; a standard-library `asyncio.to_thread` reproducer
failed there and passed outside it. `docs/runbooks/wsl-test-hangs.md` records the
diagnosis. Docker/real-Redis smoke was not run because Docker is not available
inside the current WSL environment; Redis behavior is covered with `fakeredis`.

## Explicit boundary

Shared Redis state makes cancellation terminal across replicas, but immediate
compute termination currently works only when the request reaches the process
that owns the coroutine. Distributed workers and a cancellation control channel
remain Phase 06B. The control channel was subsequently delivered in ADR 009;
durable worker reassignment remains pending.
