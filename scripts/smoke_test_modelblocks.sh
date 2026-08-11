#!/usr/bin/env bash
# Real integration smoke for the model-block subsystem (Phase 5').
# Requires the full compose stack (app + executor + minio + redis) running.
# The task asks for a KV-cache simulation; the LLM planner must emit a valid
# model call, the executor must run it, and the artifact must be downloadable.
# If the stack or a real LLM is unavailable, the script reports SKIP and
# exits 2 — a skip is not a pass.
set -euo pipefail

APP_URL="${1:-http://localhost:8000}"
TOKEN="dev:tenant-smoke:smoke-user:admin"

# 0. Preflight: catalog endpoint must list the three registered blocks.
CATALOG=$(curl -fsS "$APP_URL/api/v1/modelblocks" -H "Authorization: Bearer $TOKEN") || {
  echo "SKIP: modelblocks catalog endpoint unreachable" >&2
  exit 2
}
python - "$CATALOG" <<'PYEOF'
import json, sys
names = {entry["name"] for entry in json.loads(sys.argv[1])}
missing = {"sklearn_tabular", "torch_mlp", "kv_cache_sim"} - names
if missing:
    raise SystemExit(f"catalog missing blocks: {missing}")
print(f"preflight: catalog has {sorted(names)}")
PYEOF

# 1. Create a task that asks for a numerical KV-cache simulation. The planner
# (real LLM via capability "plan") should select kv_cache_sim.
CREATE_RESPONSE=$(curl -fsS -X POST "$APP_URL/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":"Run a numerical simulation of KV cache memory usage for a 32-layer transformer with 8 KV heads at context length 8192, across compression ratios, and summarize the memory/quality trade-off.","require_approval":false}')
TASK_ID=$(python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])' <<<"$CREATE_RESPONSE")
echo "task: $TASK_ID"

STATUS="pending"
STATUS_RESPONSE=""
for _ in $(seq 1 150); do
  STATUS_RESPONSE=$(curl -fsS "$APP_URL/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN")
  STATUS=$(python -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$STATUS_RESPONSE")
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 2
done
[[ "$STATUS" == "completed" ]]

# 2. Assert a model run executed, completed, produced metrics and artifacts.
ARTIFACT_ID=$(python - "$STATUS_RESPONSE" <<'PYEOF'
import json, sys

record = json.loads(sys.argv[1])
runs = record.get("model_runs") or []
if not runs:
    raise SystemExit(
        "no model_runs: planner did not call a block "
        "(is a real LLM configured via PK_AGENT_OLLAMA_CHAT_MODEL?)"
    )
completed = [r for r in runs if r.get("status") == "completed"]
if not completed:
    raise SystemExit(f"model run(s) failed: {[r.get('error') for r in runs]}")
run = completed[0]
if not run.get("metrics"):
    raise SystemExit(f"completed run has no metrics: {run}")
if not run.get("artifact_ids"):
    raise SystemExit(f"completed run has no artifacts: {run}")
print(run["artifact_ids"][0])
import sys as _s
print(f"model run ok: block={run['block_name']} metrics={list(run['metrics'])}", file=_s.stderr)
PYEOF
)

# 3. Artifact must be downloadable through the API.
HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
  "$APP_URL/api/v1/artifacts/$ARTIFACT_ID" -H "Authorization: Bearer $TOKEN")
[[ "$HTTP_CODE" == "200" ]] || {
  echo "FAIL: artifact $ARTIFACT_ID download returned $HTTP_CODE" >&2
  exit 1
}

echo "smoke modelblocks ok: $TASK_ID (artifact $ARTIFACT_ID)"
