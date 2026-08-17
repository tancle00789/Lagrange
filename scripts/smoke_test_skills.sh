#!/usr/bin/env bash
# Real integration smoke for the skill subsystem (Phase 7).
# Requires the full compose stack (app + postgres + redis) running with a
# real LLM (PK_AGENT_OLLAMA_CHAT_MODEL) — the distill step needs it.
# Flow: run a KV-cache task -> distill a candidate with the real LLM ->
# deterministic offline evaluate -> human-style approve -> verify the
# approved skill is matched and injected on a NEW task (via SSE events).
# If the stack or a real LLM is unavailable, the script reports SKIP and
# exits 2 — a skip is not a pass.
set -euo pipefail

APP_URL="${1:-http://localhost:8000}"
TOKEN="dev:tenant-smoke:smoke-user:admin"
AUTH="Authorization: Bearer $TOKEN"
EVENTS_FILE="$(mktemp)"
trap 'rm -f "$EVENTS_FILE"' EXIT

# 0. Preflight: skills endpoint must be reachable (empty list is fine).
curl -fsS "$APP_URL/api/v1/skills" -H "$AUTH" >/dev/null || {
  echo "SKIP: skills endpoint unreachable" >&2
  exit 2
}
echo "preflight: skills endpoint ok"

wait_for_task() { # $1 = task_id -> prints final task record JSON
  local task_id="$1" status body
  for _ in $(seq 1 150); do
    body=$(curl -fsS "$APP_URL/api/v1/tasks/$task_id" -H "$AUTH")
    status=$(python -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$body")
    if [[ "$status" == "completed" || "$status" == "failed" ]]; then
      [[ "$status" == "completed" ]] || {
        echo "FAIL: task $task_id failed" >&2
        exit 1
      }
      printf '%s' "$body"
      return 0
    fi
    sleep 2
  done
  echo "FAIL: task $task_id did not complete in time" >&2
  exit 1
}

# 1. Run a KV-cache task so distillation has a model run to generalize from.
TASK_ID=$(curl -fsS -X POST "$APP_URL/api/v1/tasks" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"input":"Run a numerical simulation of KV cache memory usage for a 32-layer transformer with 8 KV heads at context length 8192, across compression ratios, and summarize the memory/quality trade-off.","require_approval":false}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])')
echo "source task: $TASK_ID"
SOURCE_RECORD=$(wait_for_task "$TASK_ID")
# The source task must have a real completed model run — otherwise the stack
# is running a FakeModel and distillation has nothing real to generalize from.
python - "$SOURCE_RECORD" <<'PYEOF'
import json, sys

record = json.loads(sys.argv[1])
completed = [r for r in (record.get("model_runs") or []) if r.get("status") == "completed"]
if not completed:
    raise SystemExit(
        "source task has no completed model run "
        "(is a real LLM configured via PK_AGENT_OLLAMA_CHAT_MODEL?)"
    )
print(f"source task completed with model run: {completed[0]['block_name']}")
PYEOF

# 2. Distill with the real LLM (capability "distill"). Up to 3 attempts —
# a 4B local model occasionally answers prose instead of valid JSON (422).
SKILL_JSON=""
for attempt in 1 2 3; do
  DISTILL_RESPONSE=$(curl -sS -X POST "$APP_URL/api/v1/skills/distill" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"task_id\":\"$TASK_ID\"}")
  if python -c 'import sys,json; json.loads(sys.argv[1])' "$DISTILL_RESPONSE" 2>/dev/null \
     && grep -q '"detail"' <<<"$DISTILL_RESPONSE"; then
    echo "distill attempt $attempt rejected: $DISTILL_RESPONSE" >&2
    continue
  fi
  if SKILL_JSON=$(python - "$DISTILL_RESPONSE" <<'PYEOF'
import json, sys

body = json.loads(sys.argv[1])
if not body.get("worth_saving"):
    raise SystemExit(1)
record = body["record"]
keyword = ""
for cond in record.get("trigger_conditions") or []:
    if cond.get("keywords"):
        keyword = cond["keywords"][0]
        break
if not keyword:
    raise SystemExit(1)
procedure = record.get("procedure") or ""
print(json.dumps({
    "skill_id": record["skill_id"],
    "version": record["version"],
    "keyword": keyword,
    # The replay answer echoes the procedures section verbatim; a short
    # procedure prefix is a stable needle for the evaluation assertion.
    "procedure_needle": procedure[:40].strip() or record["name"],
    # Model-suggested cases from the distill response (already pinned to
    # this skill); the evaluate step prefers them over a constructed case.
    "suggested_cases": body.get("eval_cases") or [],
}))
PYEOF
); then
    break
  fi
  echo "distill attempt $attempt: model judged not worth saving or unusable triggers; retrying" >&2
done
[[ -n "$SKILL_JSON" ]] || {
  echo "FAIL: real LLM did not produce a usable candidate after 3 attempts" >&2
  exit 1
}
_field() { python -c 'import sys,json; print(json.loads(sys.argv[1])[sys.argv[2]])' "$SKILL_JSON" "$1"; }
SKILL_ID=$(_field skill_id)
SKILL_VERSION=$(_field version)
KEYWORD=$(_field keyword)
NEEDLE=$(_field procedure_needle)
echo "candidate: $SKILL_ID@$SKILL_VERSION keyword=$(printf '%q' "$KEYWORD")"

# 3. Deterministic offline evaluation: the candidate (loaded as approved)
# must match on its own trigger and its procedure must reach the answer.
# The case is constructed (needle = procedure prefix) so the gate does not
# depend on the 4B model quoting itself verbatim in suggested cases.
EVAL_RESPONSE=$(curl -fsS -X POST "$APP_URL/api/v1/skills/$SKILL_ID/$SKILL_VERSION/evaluate" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "$(python - "$KEYWORD" "$NEEDLE" "$SKILL_ID" <<'PYEOF'
import json, sys
print(json.dumps({"cases": [{
    "case_id": "smoke-1",
    "query": f"How should I approach {sys.argv[1]} for a transformer?",
    "expected_answer_contains": [sys.argv[2]],
    "expect_skill_matched": sys.argv[3],
}]}))
PYEOF
)")
python - "$EVAL_RESPONSE" <<'PYEOF'
import json, sys
body = json.loads(sys.argv[1])
rate = body["eval_result"]["eval_pass_rate"]
if body["record"]["status"] != "testing" or rate < 1.0:
    raise SystemExit(f"evaluation failed: rate={rate} details={body['eval_result']['details']}")
print(f"evaluation ok: pass_rate={rate}")
PYEOF

# 4. Approve (the human gate) and confirm publication in the registry view.
curl -fsS -X POST "$APP_URL/api/v1/skills/$SKILL_ID/$SKILL_VERSION/approve" \
  -H "$AUTH" >/dev/null || {
  echo "FAIL: approve rejected a tested candidate" >&2
  exit 1
}
LIST_RESPONSE=$(curl -fsS "$APP_URL/api/v1/skills" -H "$AUTH")
python - "$LIST_RESPONSE" "$SKILL_ID" <<'PYEOF'
import json, sys
records = json.loads(sys.argv[1])
match = [r for r in records if r["skill_id"] == sys.argv[2]]
if not match or match[0]["status"] != "approved" or not match[0].get("approved_by"):
    raise SystemExit(f"skill not published as approved: {records}")
print(f"approved by {match[0]['approved_by']}")
PYEOF

# 5. New task whose query hits the trigger: SSE must show skills_matched.
TASK2_ID=$(curl -fsS -X POST "$APP_URL/api/v1/tasks" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "$(python -c 'import json,sys; print(json.dumps({"input": f"Can you walk me through {sys.argv[1]} for a small transformer?", "require_approval": False}))' "$KEYWORD")" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])')
TICKET=$(curl -fsS -X POST "$APP_URL/api/v1/tasks/$TASK2_ID/events/ticket" -H "$AUTH" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["ticket"])')
curl -fsS -N --max-time 240 "$APP_URL/api/v1/tasks/$TASK2_ID/events?ticket=$TICKET" >"$EVENTS_FILE" 2>/dev/null &
CURL_PID=$!
wait_for_task "$TASK2_ID" >/dev/null
wait "$CURL_PID" 2>/dev/null || true
grep -q 'skills_matched' "$EVENTS_FILE" && grep -q "$SKILL_ID" "$EVENTS_FILE" || {
  echo "FAIL: no skills_matched event for $SKILL_ID on task $TASK2_ID" >&2
  sed 's/^/  event: /' "$EVENTS_FILE" >&2 || true
  exit 1
}
echo "injection ok: skills_matched event carried $SKILL_ID (PgSkillStore -> registry -> graph)"

# 6. Cleanup: deprecate the smoke skill so reruns start from a clean registry.
curl -fsS -X POST "$APP_URL/api/v1/skills/$SKILL_ID/$SKILL_VERSION/deprecate" \
  -H "$AUTH" >/dev/null

echo "smoke skills ok: source=$TASK_ID skill=$SKILL_ID@$SKILL_VERSION injected-in=$TASK2_ID"
