#!/usr/bin/env bash
# Proves role enforcement through the REAL API (via Traefik), not the UI:
#   1. log in as the bootstrap admin, invite a throwaway member, accept the invite
#   2. as that member, call every admin-only endpoint and expect 403
#   3. as that member, ask for someone else's audit rows and get only their own
#   4. as the admin, the same kind of calls succeed
#   5. disable the throwaway member again
# Exit code 0 = all checks passed. Reused by the Phase 7 DoD script.
set -uo pipefail

BASE="${BASE:-http://app.localtest.me}"
ADMIN_EMAIL=$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.email}' | base64 -d)
ADMIN_PW=$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.password}' | base64 -d)
MEMBER_EMAIL="role-check-$(date +%s)@example.com"
MEMBER_PW="role-check-$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
JARS=$(mktemp -d); trap 'rm -rf "$JARS"' EXIT
FAILED=0

# req <cookie-jar> <method> <path> [json-body] -> prints the HTTP status; body in $JARS/body
req() {
  local args=(-s -o "$JARS/body" -w '%{http_code}' -b "$JARS/$1" -c "$JARS/$1" -X "$2" "$BASE/api$3")
  [ -n "${4:-}" ] && args+=(-H 'Content-Type: application/json' -d "$4")
  curl "${args[@]}"
}
json() { python3 -c "import json,sys; d=json.load(open('$JARS/body')); print($1)"; }
check() { # description expected actual
  if [ "$2" = "$3" ]; then printf '  PASS  %-58s %s\n' "$1" "$3"; else printf '  FAIL  %-58s expected %s, got %s\n' "$1" "$2" "$3"; FAILED=1; fi
}

echo "== setup: admin logs in, invites $MEMBER_EMAIL, member accepts"
check "admin login" 200 "$(req admin POST /auth/login "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PW\"}")"
ADMIN_ID=$(json "d['id']")
check "admin invites member" 201 "$(req admin POST /users "{\"email\":\"$MEMBER_EMAIL\",\"role\":\"member\"}")"
MEMBER_ID=$(json "d['user']['id']"); TOKEN=$(json "d['invite_url'].rsplit('/',1)[1]")
check "member accepts invite (and is logged in)" 200 "$(req member POST "/invites/$TOKEN/accept" "{\"password\":\"$MEMBER_PW\"}")"
check "member session works" 200 "$(req member GET /auth/me)"

echo "== member is refused every admin endpoint (403)"
while read -r method path body; do
  [ "$body" = "-" ] && body=""
  check "member $method $path" 403 "$(req member "$method" "$path" "$body")"
done <<LIST
GET /users -
POST /users {"email":"sneaky@example.com"}
PATCH /users/$ADMIN_ID {"role":"member"}
PATCH /users/$MEMBER_ID {"role":"admin"}
POST /users/$ADMIN_ID/invite -
GET /users/$ADMIN_ID/keys -
POST /users/$MEMBER_ID/keys {"name":"self-issued"}
POST /servers {"slug":"evil","name":"Evil","url":"https://mcp.example.com/mcp"}
PATCH /servers/fx {"enabled":false}
DELETE /servers/fx -
POST /servers/fx/test -
LIST

echo "== member sees only their own data"
check "member GET /audit?user_id=$ADMIN_ID (allowed, but filtered)" 200 "$(req member GET "/audit?user_id=$ADMIN_ID")"
check "  ...every returned row is the member's own" True "$(json "all(i['user_id'] == $MEMBER_ID for i in d['items'])")"
check "member can view servers (read-only)" 200 "$(req member GET /servers)"
check "member can create own API key" 201 "$(req member POST /me/keys '{"name":"role-check"}')"

echo "== admin can do the same things"
check "admin GET /users" 200 "$(req admin GET /users)"
check "admin POST /servers/fx/test" 200 "$(req admin POST /servers/fx/test)"
check "admin GET /audit (all users)" 200 "$(req admin GET '/audit?limit=1')"

echo "== cleanup"
check "admin disables the throwaway member" 200 "$(req admin PATCH "/users/$MEMBER_ID" '{"status":"disabled"}')"
check "disabled member's session is dead" 401 "$(req member GET /auth/me)"

[ "$FAILED" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$FAILED"
