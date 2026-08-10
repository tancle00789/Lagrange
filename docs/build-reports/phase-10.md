# Phase 10 Build Report — Browser and Deployment Verification

Date: 2026-08-05
Status: implementation complete; container runtime verified; host-port verifier pending WSL forwarding

## Scope delivered

- Added Playwright and a Chromium task-flow E2E covering token entry, authorized
  task creation, request payload, refreshed task selection, grounded report and
  citation rendering.
- Added frontend production build/E2E scripts and Make targets.
- Added an application container healthcheck and a deterministic core deployment
  verifier for health, durable task completion, cross-tenant denial, Redis
  Streams state, forced PostgreSQL RLS and Prometheus metrics.
- Added `.dockerignore` after a real build exposed that local virtualenv and
  frontend dependencies were entering the build context.
- Pinned the container's `uv` installer and changed the runtime image to a
  non-root UID with owned application/static files.
- Added the production readiness checklist and portfolio README.
- Fixed the production startup path so the tenant-scoped PostgreSQL checkpointer
  inherits LangGraph's `BaseCheckpointSaver` contract; the app now boots against
  the real PostgreSQL/Redis core stack.

## Verification completed

| Gate | Result |
|---|---|
| Frontend `npm run build` | passed, Vite 8.2.0 |
| Chromium Playwright E2E | 1 passed |
| npm audit | 0 vulnerabilities |
| Compose YAML/config parse | passed |
| Deployment verifier shell syntax | passed |
| Core containers after checkpointer fix | app healthy; task completed; cross-tenant read returned 404; Redis group pending=0; 3 forced RLS policies; Prometheus job metric emitted |

## Live deployment incident

Docker Desktop 28.5.2 initially connected and began pulling/building the core
stack. After that build was intentionally interrupted to fix the missing
`.dockerignore`, the Docker Linux engine returned API 500. A clean Desktop
restart reproduced the failure. Host logs show the `docker-desktop` VM stuck
waiting for filesystem, `eth0` and metadata readiness, followed by WSL vsock
timeouts. Even `wsl.exe --terminate docker-desktop` could not cross the failed
vsock boundary from the current Ubuntu session.

After restarting Docker Desktop/WSL, the app container and its internal health
check recovered. The remaining host-side gate is the verifier's access to
`localhost:8000`: Docker Desktop currently reports the published port through
its Desktop proxy, while this WSL session cannot reach that proxy even though
the app is healthy inside the Docker network. The internal-network checks above
are evidence for the runtime path, but the host-side script is not marked as a
full pass until the port-forwarding path is restored.

The remaining command is:

```bash
docker compose up -d --build db redis app
DOCKER_CMD=docker.exe bash scripts/verify_core_deployment.sh
```

No live-container/RLS result is claimed until that command succeeds.
