#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
TOKEN="dev:tenant-smoke:smoke-user:admin"

curl -fsS "$BASE_URL/health" | grep -q '"status":"ok"'

CREATE_RESPONSE=$(curl -fsS -X POST "$BASE_URL/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-$(date +%s)" \
  -d '{"input":"smoke echo","require_approval":false}')

TASK_ID=$(python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])' <<<"$CREATE_RESPONSE")

for _ in $(seq 1 30); do
  STATUS_RESPONSE=$(curl -fsS "$BASE_URL/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN")
  STATUS=$(python -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$STATUS_RESPONSE")
  if [[ "$STATUS" == "completed" ]]; then
    break
  fi
  sleep 1
done

[[ "$STATUS" == "completed" ]]
echo "smoke ok: $TASK_ID"
