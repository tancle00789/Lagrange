#!/usr/bin/env bash
# Real integration smoke against the composed WeKnora subsystem.
# Not run in CI by default: requires weknora-app healthy, an embedding model
# reachable from the weknora-app container (default: Ollama on the host), and
# bootstrap env vars from scripts/init_weknora.py.
set -euo pipefail

APP_URL="${1:-http://localhost:8000}"
WEKNORA_URL="${WEKNORA_BASE_URL:-http://localhost:8081}"
TOKEN="dev:tenant-smoke:smoke-user:admin"

: "${PK_AGENT_WEKNORA_API_KEY:?run scripts/init_weknora.py and export its output first}"
: "${PK_AGENT_WEKNORA_DEFAULT_KB_ID:?run scripts/init_weknora.py and export its output first}"

MARKER="quixotic-zephyr-$(date +%s)"
DOC_CONTENT="The $MARKER protocol describes a fictional telemetry handshake used only for smoke testing."

# 1. Ingest a small document directly through the WeKnora API.
# cwd-relative path: absolute /tmp paths get mangled by MSYS arg conversion
# once ;filename= modifiers are appended on Windows git-bash curl.
DOC_FILE="smoke-doc-$$.txt"
trap 'rm -f "$DOC_FILE"' EXIT
printf '%s' "$DOC_CONTENT" > "$DOC_FILE"
UPLOAD_RESPONSE=$(curl -fsS -X POST \
  "$WEKNORA_URL/api/v1/knowledge-bases/$PK_AGENT_WEKNORA_DEFAULT_KB_ID/knowledge/file" \
  -H "X-API-Key: $PK_AGENT_WEKNORA_API_KEY" \
  -F "file=@$DOC_FILE;filename=smoke-$MARKER.txt;type=text/plain")
KNOWLEDGE_ID=$(python -c 'import sys,json; print(json.load(sys.stdin)["data"]["id"])' <<<"$UPLOAD_RESPONSE")
echo "uploaded knowledge: $KNOWLEDGE_ID"

# 2. Wait for parsing/embedding to finish.
PARSE_STATUS="unknown"
for _ in $(seq 1 60); do
  KNOWLEDGE=$(curl -fsS "$WEKNORA_URL/api/v1/knowledge/$KNOWLEDGE_ID" \
    -H "X-API-Key: $PK_AGENT_WEKNORA_API_KEY")
  PARSE_STATUS=$(python -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(d.get("parse_status") or d.get("status") or "unknown")' <<<"$KNOWLEDGE")
  if [[ "$PARSE_STATUS" == "completed" || "$PARSE_STATUS" == "success" ]]; then
    break
  fi
  if [[ "$PARSE_STATUS" == "failed" ]]; then
    echo "ingestion failed for $KNOWLEDGE_ID" >&2
    exit 1
  fi
  sleep 2
done
echo "parse status: $PARSE_STATUS"

# 3. Create a pk_agent task whose query matches the ingested document.
CREATE_RESPONSE=$(curl -fsS -X POST "$APP_URL/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"input\":\"what is the $MARKER protocol?\",\"require_approval\":false}")
TASK_ID=$(python -c 'import sys,json; print(json.load(sys.stdin)["task_id"])' <<<"$CREATE_RESPONSE")

STATUS="pending"
for _ in $(seq 1 30); do
  STATUS_RESPONSE=$(curl -fsS "$APP_URL/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN")
  STATUS=$(python -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$STATUS_RESPONSE")
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    break
  fi
  sleep 1
done

[[ "$STATUS" == "completed" ]]

# 4. Assert a real WeKnora chunk carrying the marker reached the task state.
# Checking knowledge_hits (not result) keeps this robust for both the
# deterministic FakeModel and a real LLM whose phrasing may vary.
python - "$STATUS_RESPONSE" <<'PYEOF'
import json, sys

record = json.loads(sys.argv[1])
hits = record.get("knowledge_hits") or []
if not any("quixotic-zephyr" in (hit.get("snippet") or "") for hit in hits):
    raise SystemExit(f"expected smoke marker in knowledge_hits, got: {hits[:1]}")
report = record.get("report") or {}
if not report.get("grounded"):
    raise SystemExit(f"expected grounded report, got: {report}")
print("real weknora smoke ok")
PYEOF

echo "smoke weknora ok: $TASK_ID"
