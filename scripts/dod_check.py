#!/usr/bin/env python3
"""Definition-of-Done check: walks the CLAUDE.md DoD end to end against the
running cluster and prints a checklist. Standard library only (urllib for HTTP,
plain JSON-RPC for MCP), plus `kubectl` for cluster actions.

    make dod-check

What it does (each step prints PASS/FAIL/SKIP):
  1. storefront checkout with Cash on Delivery (WooCommerce Store API)
  2. two users, A and B, invited by the admin and accepting their invites
  3. A uses chat: creates a product (visible on the storefront) and asks for
     today's orders (gets the order from step 1)
  4. B uses the MCP endpoint with only the gateway URL + an API key: sees every
     server's tools, lists today's orders, does a two-upstream flow (woo -> fx)
  5. B's key revoked -> B's next call is rejected
  6. fx disabled -> its tools disappear for MCP clients and the chat agent
  7. notes-mcp pod deleted -> Kubernetes replaces it and it works again (self-healing);
     then scaled to 0 -> others keep working, dashboard API shows Unreachable
  8. every call above is in the audit log under the right user and channel
  9. the member-vs-admin role check (scripts/api-role-check.sh)
It leaves an order and a test product behind (like the audit log, they are the
evidence) and disables the two test users at the end.
"""

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Override for the VPS: APP_URL=https://app.<domain> SHOP_URL=https://shop.<domain>
APP = os.environ.get("APP_URL", "http://app.localtest.me").rstrip("/")
SHOP = os.environ.get("SHOP_URL", "http://shop.localtest.me").rstrip("/")
NO_CHAT = "--no-chat" in sys.argv     # skip the LLM steps entirely (no request reaches the model)
RUN = str(int(time.time()))
results: list[tuple[str, str, str]] = []


# --- tiny helpers ------------------------------------------------------------------

def record(status: str, what: str, detail: str = "") -> bool:
    results.append((status, what, detail))
    mark = {"PASS": "  PASS", "FAIL": "  FAIL", "SKIP": "  SKIP"}[status]
    print(f"{mark}  {what}" + (f"  ({detail})" if detail else ""), flush=True)
    return status == "PASS"


def check(ok: bool, what: str, detail: str = "") -> bool:
    return record("PASS" if ok else "FAIL", what, detail)


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True).stdout


def secret(name: str, key: str) -> str:
    import base64
    return base64.b64decode(kubectl("-n", "platform", "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}")).decode()


class Http:
    """A browser-like client: keeps cookies, sends/receives JSON."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, url: str, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})})
        try:
            with self.opener.open(req, timeout=120) as resp:
                return resp.status, resp.headers, resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read().decode()

    def api(self, method: str, path: str, body=None):
        status, _, text = self.request(method, APP + "/api" + path, body)
        try:
            return status, json.loads(text) if text else None
        except ValueError:
            return status, text


def mcp(key: str, method: str, params: dict) -> tuple[int, dict | None]:
    """One JSON-RPC request to the gateway's /mcp, as an external client would send it."""
    req = urllib.request.Request(APP + "/mcp", method="POST",
                                 data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream",
                                          "MCP-Protocol-Version": "2025-11-25"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, None
    # The gateway may answer with plain JSON or a one-event SSE stream.
    for line in text.splitlines():
        if line.startswith("data:"):
            text = line[5:].strip()
    message = json.loads(text)
    return 200, message.get("result")


def tool(key: str, name: str, args: dict) -> tuple[bool, str]:
    status, result = mcp(key, "tools/call", {"name": name, "arguments": args})
    if status != 200 or result is None:
        return False, f"HTTP {status}"
    text = "".join(c.get("text", "") for c in result.get("content", []))
    return not result.get("isError", False), text


def tool_names(key: str) -> set[str]:
    status, result = mcp(key, "tools/list", {})
    return {t["name"] for t in (result or {}).get("tools", [])} if status == 200 else set()


MAX_CHAT_WAIT = 90          # seconds; a longer Retry-After means a daily quota, not worth waiting for
llm_quota_exhausted: str | None = None


def chat(user: "Http", text: str):
    """POST /api/chat; if the model's per-minute rate limit was hit (free tiers allow
    only a few requests per minute), wait as the gateway's Retry-After says and retry.
    A long Retry-After (daily quota used up) is not waited for: the chat checks are
    then reported as SKIPPED with the reason."""
    global llm_quota_exhausted
    if llm_quota_exhausted:
        return 429, {"detail": llm_quota_exhausted}
    for attempt in range(3):
        status, headers, body = user.request("POST", APP + "/api/chat", {"messages": [{"role": "user", "text": text}]})
        if status != 429 or attempt == 2:
            break
        wait = int(headers.get("Retry-After", "30"))
        if wait > MAX_CHAT_WAIT:
            llm_quota_exhausted = f"LLM quota exhausted, provider says retry in {wait // 3600}h{wait % 3600 // 60:02d}m"
            return 429, {"detail": llm_quota_exhausted}
        print(f"        (AI model rate limit; waiting {wait}s as the gateway advises, then retrying)", flush=True)
        time.sleep(wait + 1)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {"detail": body[:200]}


def wait_for(predicate, timeout: float, interval: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# --- the checks -------------------------------------------------------------------------

def storefront_order() -> dict | None:
    print("\n== 1. Storefront checkout with Cash on Delivery")
    shop = Http()
    status, _, text = shop.request("GET", SHOP + "/wp-json/wc/store/v1/products?search=Masala")
    products = json.loads(text) if status == 200 else []
    if not check(bool(products), "storefront lists products", f"{len(products)} match 'Masala'"):
        return None
    status, headers, _ = shop.request("GET", SHOP + "/wp-json/wc/store/v1/cart")
    auth = {"Nonce": headers.get("Nonce", ""), "Cart-Token": headers.get("Cart-Token", "")}
    status, _, _ = shop.request("POST", SHOP + "/wp-json/wc/store/v1/cart/add-item",
                                {"id": products[0]["id"], "quantity": 2}, auth)
    check(status in (200, 201), "add to cart", f"HTTP {status}")
    address = {"first_name": "DoD", "last_name": f"Check {RUN}", "address_1": "1 MG Road", "city": "Bengaluru",
               "state": "KA", "postcode": "560001", "country": "IN", "email": f"dod-{RUN}@example.com",
               "phone": "9999999999"}
    status, _, text = shop.request("POST", SHOP + "/wp-json/wc/store/v1/checkout",
                                   {"billing_address": address, "shipping_address": address, "payment_method": "cod"}, auth)
    order = json.loads(text) if text else {}
    ok = check(status == 200 and order.get("order_id") and order.get("payment_method") == "cod",
               "checkout with COD", f"order #{order.get('order_id')} status {order.get('status')}" if status == 200 else text[:200])
    return order if ok else None


def invite(admin: Http, label: str) -> tuple[Http, int, str]:
    email = f"dod-{label}-{RUN}@example.com"
    status, body = admin.api("POST", "/users", {"email": email, "role": "member"})
    user = Http()
    token = body["invite_url"].rsplit("/", 1)[1]
    accepted, me = user.api("POST", f"/invites/{token}/accept", {"password": f"dod-{RUN}-password"})
    check(status == 201 and accepted == 200, f"user {label.upper()} invited and accepted", email)
    return user, me["id"], email


def main() -> int:
    print(f"Definition-of-Done check, run {RUN}")
    admin = Http()
    status, _ = admin.api("POST", "/auth/login", {"email": secret("gateway-admin", "email"),
                                                  "password": secret("gateway-admin", "password")})
    if not check(status == 200, "admin login (bootstrap admin from Secret)"):
        return 1

    order = storefront_order()
    order_id = order["order_id"] if order else None

    print("\n== 2. Two users")
    user_a, a_id, a_email = invite(admin, "a")
    user_b, b_id, b_email = invite(admin, "b")
    _, created = user_b.api("POST", "/me/keys", {"name": "dod"})
    b_key, b_key_id = created["key"], created["item"]["id"]
    check(b_key.startswith("urk_"), "user B created an API key (shown once)", created["item"]["prefix"])

    print("\n== 3. User A uses chat")
    product_name = f"DoD Product {RUN}"
    if NO_CHAT:
        status, reply = 503, {}
    else:
        status, reply = chat(user_a, f"Create a product called {product_name} priced 499.")
    chat_ok = status == 200
    if status == 503:
        record("SKIP", "chat steps", "--no-chat given" if NO_CHAT else "no LLM key configured (make llm-secret)")
    elif llm_quota_exhausted:
        chat_ok = False
        record("SKIP", "chat steps (A creates a product, asks for today's orders)", llm_quota_exhausted)
    else:
        calls = [c["name"] for c in reply.get("tool_calls", [])] if chat_ok else []
        check(chat_ok and "woo__create_product" in calls, "A (chat) creates a product",
              f"tools used: {calls}" if chat_ok else f"HTTP {status}: {reply.get('detail')}")
        found = wait_for(lambda: json.loads(Http().request(
            "GET", SHOP + "/wp-json/wc/store/v1/products?search=" + urllib.request.quote(product_name))[2] or "[]"), 20)
        check(bool(found), "the product appears on the storefront", product_name)
        status, reply = chat(user_a,
            "What orders came in today? List each order's number.")
        calls = [c["name"] for c in reply.get("tool_calls", [])] if status == 200 else []
        if llm_quota_exhausted:
            record("SKIP", "A (chat) asks for today's orders", llm_quota_exhausted)
        else:
            check(status == 200 and "woo__list_orders" in calls and order_id is not None and str(order_id) in reply["answer"],
                  "A (chat) asks for today's orders and gets the new order",
                  f"order #{order_id}, tools {calls}" if status == 200 else f"HTTP {status}: {reply.get('detail')}")

    print("\n== 4. User B uses the MCP endpoint (gateway URL + API key only)")
    names = tool_names(b_key)
    servers = {n.split("__")[0] for n in names}
    check({"woo", "notes", "fx"} <= servers, "B sees tools from every enabled server",
          f"{len(names)} tools from {sorted(servers)}")
    ok, text = tool(b_key, "woo__list_orders", {"date": "today"})
    orders = json.loads(text).get("orders", []) if ok else []
    check(ok and any(o["id"] == order_id for o in orders), "B lists today's orders and sees the new order",
          f"{len(orders)} order(s) today")
    total = sum(float(o["total"]) for o in orders) if orders else 0
    ok, text = tool(b_key, "fx__convert_currency", {"amount": total, "from_currency": "INR", "to_currency": "USD"})
    converted = json.loads(text).get("converted") if ok else None
    check(ok and converted is not None, "B: two-upstream flow (woo orders total -> fx convert to USD)",
          f"₹{total:.2f} = ${converted}")

    print("\n== 5. Revoke B's key")
    status, _ = admin.api("DELETE", f"/keys/{b_key_id}")
    rejected, _ = mcp(b_key, "tools/call", {"name": "fx__list_rates", "arguments": {}})
    check(status == 204 and rejected == 401, "after revocation, B's next call is rejected", f"HTTP {rejected}")
    _, created = user_b.api("POST", "/me/keys", {"name": "dod-2"})
    b_key2 = created["key"]

    print("\n== 6. Disable an upstream (fx)")
    status, _ = admin.api("PATCH", "/servers/fx", {"enabled": False})
    try:
        names = tool_names(b_key2)
        check(status == 200 and not any(n.startswith("fx__") for n in names),
              "fx tools disappear for the external client (immediately)", f"PATCH {status}, {len(names)} tools left")
        if chat_ok:
            status, reply = chat(user_a, "Convert 100 INR to USD.")
            used = [c["name"] for c in reply.get("tool_calls", []) if c["ok"]] if status == 200 else []
            if llm_quota_exhausted:
                record("SKIP", "the chat agent no longer has fx tools", llm_quota_exhausted)
            else:
                check(status == 200 and not any(n.startswith("fx__") for n in used),
                      "the chat agent no longer has fx tools",
                      f"successful tool calls: {used}" if status == 200 else f"HTTP {status}: {reply.get('detail')}")
    finally:
        # Always restore, even if a check above blew up: never leave fx disabled.
        status, _ = admin.api("PATCH", "/servers/fx", {"enabled": True})
    check(status == 200 and any(n.startswith("fx__") for n in tool_names(b_key2)),
          "fx re-enabled: its tools are back", f"PATCH {status}")

    print("\n== 7. Kill an upstream pod (notes-mcp)")
    def notes_status():
        _, servers = admin.api("GET", "/servers")
        return next((s["status"] for s in servers if s["slug"] == "notes"), None) if isinstance(servers, list) else None

    def ready_notes_pods() -> list[str]:
        out = kubectl("-n", "platform", "get", "pods", "-l", "app.kubernetes.io/name=notes-mcp", "-o",
                      "jsonpath={range .items[*]}{.metadata.name} {.status.conditions[?(@.type=='Ready')].status} "
                      "{.metadata.deletionTimestamp}{'\\n'}{end}")
        return [line.split()[0] for line in out.splitlines()
                if line.split()[1:2] == ["True"] and len(line.split()) == 2]   # Ready and not terminating

    # 7a. Self-healing: delete the pod; its Deployment notices and starts a replacement.
    [old_pod] = ready_notes_pods()[:1] or [None]
    kubectl("-n", "platform", "delete", "pod", old_pod, "--wait=false")
    new_pods = wait_for(lambda: [p for p in ready_notes_pods() if p != old_pod], 120)
    check(bool(new_pods), "self-healing: deleted notes-mcp pod is replaced by its Deployment",
          f"{old_pod} -> {new_pods[0] if new_pods else 'none'}")
    healed = wait_for(lambda: tool(b_key2, "notes__list_notes", {"limit": 1})[0], 45)
    check(healed, "notes tools work again through the gateway (no manual action)")

    # 7b. A real outage: no pod at all.
    kubectl("-n", "platform", "scale", "deploy/notes-mcp", "--replicas=0")
    try:
        unreachable = wait_for(lambda: notes_status() == "unreachable", 60)
        check(unreachable, "dashboard API shows notes as Unreachable")
        ok_woo, _ = tool(b_key2, "woo__list_products", {"per_page": 1})
        ok_fx, _ = tool(b_key2, "fx__list_rates", {})
        check(ok_woo and ok_fx, "the other upstreams keep working", f"woo={ok_woo} fx={ok_fx}")
    finally:
        # Always bring it back, even if a check above blew up.
        kubectl("-n", "platform", "scale", "deploy/notes-mcp", "--replicas=1")
        kubectl("-n", "platform", "rollout", "status", "deploy/notes-mcp", "--timeout=120s")
    check(wait_for(lambda: notes_status() == "connected", 60), "notes back to Connected after restart")

    print("\n== 8. Audit log: every call under the right user and channel")
    _, a_audit = admin.api("GET", f"/audit?user_id={a_id}&limit=200")
    _, b_audit = admin.api("GET", f"/audit?user_id={b_id}&limit=200")
    a_rows, b_rows = a_audit["items"], b_audit["items"]
    if llm_quota_exhausted and chat_ok:
        record("SKIP", "A's calls are audited as A, channel chat", "not all chat steps ran: " + llm_quota_exhausted)
    elif chat_ok:
        check(a_rows and all(r["channel"] == "chat" and r["user_email"] == a_email for r in a_rows)
              and {"woo__create_product", "woo__list_orders"} <= {r["tool"] for r in a_rows},
              "A's calls are audited as A, channel chat", f"{len(a_rows)} rows")
    b_tools = {r["tool"] for r in b_rows}
    check(b_rows and all(r["channel"] == "api" and r["user_email"] == b_email for r in b_rows)
          and {"woo__list_orders", "fx__convert_currency"} <= b_tools,
          "B's calls are audited as B, channel api", f"{len(b_rows)} rows")
    check(any(r["outcome"] == "denied" and r["tool"] == "fx__list_rates" for r in b_rows),
          "B's rejected call after revocation is audited as denied")

    print("\n== 9. Roles enforced by the API (member vs admin)")
    role_check = subprocess.run(["bash", "scripts/api-role-check.sh"], capture_output=True, text=True,
                                env={**os.environ, "BASE": APP})
    check(role_check.returncode == 0, "member is refused every admin endpoint (scripts/api-role-check.sh)",
          role_check.stdout.strip().splitlines()[-1] if role_check.stdout else "")

    print("\n== Cleanup")
    for uid in (a_id, b_id):
        admin.api("PATCH", f"/users/{uid}", {"status": "disabled"})
    print(f"  test users disabled; order #{order_id} and product '{product_name}' kept as evidence")

    passed = sum(1 for s, _, _ in results if s == "PASS")
    failed = [w for s, w, _ in results if s == "FAIL"]
    skipped = sum(1 for s, _, _ in results if s == "SKIP")
    print(f"\n{passed} passed, {len(failed)} failed, {skipped} skipped")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
