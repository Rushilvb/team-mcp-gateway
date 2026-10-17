#!/usr/bin/env bash
# Sends one prompt to the chat agent as the bootstrap admin, through the real
# Ingress, and prints the answer, the tool calls it made, and the newest chat audit rows.
#   make chat-check PROMPT="What orders came in today?"
set -euo pipefail
PROMPT="${1:-What orders came in today?}"
BASE="${BASE:-http://app.localtest.me}"
EMAIL=$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.email}' | base64 -d)
PW=$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.password}' | base64 -d)
JAR=$(mktemp); trap 'rm -f "$JAR"' EXIT
curl -sf -o /dev/null -c "$JAR" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" "$BASE/api/auth/login"

BODY=$(python3 -c 'import json, sys; print(json.dumps({"messages": [{"role": "user", "text": sys.argv[1]}]}))' "$PROMPT")
echo "> $PROMPT"
REPLY=$(curl -s -b "$JAR" -H 'Content-Type: application/json' -d "$BODY" "$BASE/api/chat")
python3 - "$REPLY" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
if "detail" in r:
    sys.exit("error: " + str(r["detail"]))
print("\n" + r["answer"] + f"\n\n[{r['model']}, {r['steps']} step(s)]")
for c in r["tool_calls"]:
    status = "ok " if c["ok"] else "ERR"
    print(f"  {status} {c['name']}({json.dumps(c['arguments'])})")
PY

echo; echo "Latest chat audit rows:"
AUDIT=$(curl -s -b "$JAR" "$BASE/api/audit?limit=5&channel=chat")
python3 - "$AUDIT" <<'PY'
import json, sys
for a in json.loads(sys.argv[1])["items"]:
    print(f"  {a['started_at'][:19]}  {a['user_email']:<22} {a['channel']:<5} {a['tool']:<28} {a['outcome']}")
PY
