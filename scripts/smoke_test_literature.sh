#!/usr/bin/env bash
# Real integration smoke for the literature subsystem (Phase 4').
# Hits the REAL arXiv API through the app container. Not run in CI by default.
# If the public internet is unreachable from this machine (known local TLS
# interception on some registries), the script reports SKIP and exits 2 —
# a skip is not a pass.
set -euo pipefail

APP_URL="${1:-http://localhost:8000}"
TOKEN="dev:tenant-smoke:smoke-user:admin"

# 0. Reachability preflight: the app must be able to reach arXiv for this
# smoke to mean anything.
PREFLIGHT=$(curl -fsS -X POST "$APP_URL/api/v1/literature/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"kv cache compression","sources":["arxiv"],"limit":2}') || {
  echo "SKIP: literature/search endpoint unreachable" >&2
  exit 2
}
RECORD_COUNT=$(python -c 'import sys,json; print(len(json.load(sys.stdin)["records"]))' <<<"$PREFLIGHT")
if [[ "$RECORD_COUNT" == "0" ]]; then
  ERRORS=$(python -c 'import sys,json; print(json.load(sys.stdin)["errors"])' <<<"$PREFLIGHT")
  echo "SKIP: arXiv unreachable from app container ($ERRORS)" >&2
  exit 2
fi
echo "preflight: arxiv returned $RECORD_COUNT record(s)"

# 1. Create a task on an obscure topic (no internal docs) with external search
# enabled, so the graph takes the retrieve_external branch.
QUERY="asynchronous kv cache eviction for trillion parameter mixture-of-experts $(date +%s)"
CREATE_RESPONSE=$(curl -fsS -X POST "$APP_URL/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"input\":\"$QUERY\",\"require_approval\":false,\"allow_external\":true}")
TASK_ID=$(python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])' <<<"$CREATE_RESPONSE")
echo "task: $TASK_ID"

STATUS="pending"
for _ in $(seq 1 60); do
  STATUS_RESPONSE=$(curl -fsS "$APP_URL/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN")
  STATUS=$(python -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$STATUS_RESPONSE")
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 2
done
[[ "$STATUS" == "completed" ]]

# 2. Assert real external literature reached the task state and every report
# citation resolves to a known chunk_id or source_id.
python - "$STATUS_RESPONSE" <<'PYEOF'
import json, sys

record = json.loads(sys.argv[1])
sources = record.get("external_sources") or []
if not sources:
    raise SystemExit(f"expected external_sources from real arxiv, got: {record.get('status')}")
for item in sources:
    if item.get("source_type") not in {"arxiv", "github", "crossref", "nature"} \
            or not item.get("title"):
        raise SystemExit(f"unexpected external record shape: {item}")
known = {hit["chunk_id"] for hit in record.get("knowledge_hits") or []}
known |= {f"{s['source_type']}:{s['external_id']}" for s in sources}
report = record.get("report") or {}
for claim in report.get("claims") or []:
    unknown = set(claim.get("chunk_ids") or []) - known
    if unknown:
        raise SystemExit(f"claim cites unknown ids {unknown}: {claim}")
print(f"real literature smoke ok: {len(sources)} external source(s), "
      f"grounded={report.get('grounded')}")
PYEOF

echo "smoke literature ok: $TASK_ID"
