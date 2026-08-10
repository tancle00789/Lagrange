# WSL Test Hangs Runbook

## Symptom

- `fastapi.testclient.TestClient` hangs in `__enter__`, or
- a LangGraph async test passes its assertions but pytest never advances or
  exits.

## Diagnosis

First test the Python runtime without importing pk-agent:

```bash
python -c 'import asyncio; asyncio.run(asyncio.to_thread(lambda: 42)); print("done")'
```

If `42` is computed but `done` never prints, the environment cannot wake an
asyncio loop from a worker thread. Starlette TestClient uses an AnyIO blocking
portal, and LangGraph's in-memory checkpoint path can use the default executor;
both expose the same environmental limitation.

This was reproduced inside the Codex managed WSL sandbox. The same interpreter,
environment and test suite pass when run in the normal WSL process namespace.
It is not a pk-agent deadlock and should not be worked around by weakening task
lifecycle code or adding arbitrary pytest timeouts.

## Resolution

1. Install the locked development dependencies. Starlette 1.3+ TestClient uses
   `httpx2`; it is explicitly included in the dev group.

   ```bash
   uv sync --frozen
   ```

2. Run tests from a normal WSL/Windows terminal rather than a restricted
   container or managed sandbox that blocks cross-thread event-loop wakeups.

   ```bash
   uv run pytest
   ```

3. In Codex, run the pytest command with sandbox escalation. The test itself
   needs no network access; it only needs normal thread/event-loop semantics.

## Evidence from the 2026-08-05 incident

- Restricted sandbox: the standard-library `asyncio.to_thread` reproducer
  returned its value and then hung during event-loop shutdown.
- Outside the restricted sandbox: the reproducer printed `42` and `done`.
- After locking `httpx2`, all 80 pk-agent tests passed outside the restricted
  sandbox without TestClient deprecation warnings.
