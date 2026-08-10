#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
DOCKER_CMD="${DOCKER_CMD:-docker}"
TOKEN_A="dev:tenant-deploy-a:deploy-user:admin"
TOKEN_B="dev:tenant-deploy-b:deploy-user:admin"

compose() {
  "$DOCKER_CMD" compose "$@"
}

echo "[1/6] Compose and container health"
compose config --quiet
compose ps
curl -fsS "$BASE_URL/health" | grep -q '"status":"ok"'

echo "[2/6] Durable task execution"
CREATE_RESPONSE=$(curl -fsS -X POST "$BASE_URL/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: deploy-$(date +%s)" \
  -d '{"input":"deployment verification","require_approval":false}')
TASK_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<<"$CREATE_RESPONSE")

STATUS="pending"
for _ in $(seq 1 60); do
  RESPONSE=$(curl -fsS "$BASE_URL/api/v1/tasks/$TASK_ID" \
    -H "Authorization: Bearer $TOKEN_A")
  STATUS=$(python -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$RESPONSE")
  [[ "$STATUS" == "completed" ]] && break
  [[ "$STATUS" == "failed" ]] && break
  sleep 1
done
[[ "$STATUS" == "completed" ]]

echo "[3/6] Cross-tenant API isolation"
HTTP_STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "$BASE_URL/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN_B")
[[ "$HTTP_STATUS" == "404" ]]

echo "[4/6] Redis Streams consumer group"
compose exec -T redis redis-cli XINFO GROUPS pk_agent:jobs | grep -q 'pk_agent:workers'
compose exec -T redis redis-cli XPENDING pk_agent:jobs pk_agent:workers

echo "[5/6] PostgreSQL forced RLS"
POLICIES=$(compose exec -T db psql -U postgres -d pk_agent -Atc \
  "SELECT count(*) FROM pg_policies WHERE policyname LIKE 'pk_agent_%_tenant'")
[[ "$POLICIES" == "3" ]]
UNSCOPED=$(compose exec -T db psql -U postgres -d pk_agent -Atc \
  "SELECT count(*) FROM checkpoints")
[[ "$UNSCOPED" == "0" ]]
SCOPED=$(compose exec -T db psql -U postgres -d pk_agent -Atc \
  "BEGIN; SELECT set_config('pk_agent.tenant_id','tenant-deploy-a',true); SELECT count(*) FROM checkpoints; COMMIT" \
  | tail -2 | head -1)
[[ "$SCOPED" -gt 0 ]]

echo "[6/6] Metrics contract"
curl -fsS "$BASE_URL/metrics" | grep -q 'pk_agent_http_requests_total'
curl -fsS "$BASE_URL/metrics" | grep -q 'pk_agent_jobs_total'

echo "deployment verification ok: task=$TASK_ID status=$STATUS rls_policies=$POLICIES"
