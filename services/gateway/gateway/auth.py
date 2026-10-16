"""Authentication for the /mcp endpoint (pure ASGI wrapper, like Phase 3's RequireHeader).

Every request must carry `Authorization: Bearer urk_...`. The key is hashed and
looked up on EVERY request (no cache), so a revocation applies to the very next
call. On success the caller's Principal is put in scope["state"], where the MCP
handlers read it as ctx.request.state.principal.

If a key is recognised but revoked (or its user disabled), and the request was
a tools/call, we also write a "denied" audit row naming the user and the tool.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import naming
from .audit import Audit, Principal, Timer
from .models import ApiKey, User
from .security import KEY_PREFIX, hash_api_key

log = logging.getLogger(__name__)
MAX_BODY_FOR_AUDIT = 64 * 1024


class Authenticator:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def authenticate(self, authorization: str) -> tuple[Principal | None, int | None, str]:
        """Return (principal, user_id_if_known, reason). principal is None when refused."""
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX):
            return None, None, "missing or malformed bearer token"
        async with self._sm() as session:
            row = (await session.execute(
                select(ApiKey, User).join(User, ApiKey.user_id == User.id)
                .where(ApiKey.key_hash == hash_api_key(token))
            )).first()
            if row is None:
                return None, None, "unknown API key"
            key, user = row
            if key.revoked_at is not None:
                return None, user.id, "API key revoked"
            if user.status != "active":
                return None, user.id, f"user is {user.status}"
            await session.execute(update(ApiKey).where(ApiKey.id == key.id)
                                  .values(last_used_at=datetime.now(timezone.utc)))
            await session.commit()
            return Principal(user.id, user.email, user.role, "api", key.id), user.id, "ok"


class McpAuth:
    def __init__(self, app, authenticator: Authenticator, audit: Audit):
        self.app = app
        self.authenticator = authenticator
        self.audit = audit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        timer = Timer()
        authorization = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        principal, known_user_id, reason = await self.authenticator.authenticate(authorization)
        if principal is not None:
            scope.setdefault("state", {})["principal"] = principal
            return await self.app(scope, receive, send)

        if known_user_id is not None:
            await self._audit_denied(receive, known_user_id, reason, timer)
        body = json.dumps({"error": "unauthorized", "message": reason}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="mcp"'),
        ]})
        await send({"type": "http.response.body", "body": body})

    async def _audit_denied(self, receive, user_id: int, reason: str, timer: Timer) -> None:
        """Peek at the (small) JSON-RPC body: if it was a tools/call, record who tried what."""
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body") or len(body) > MAX_BODY_FOR_AUDIT:
                break
        try:
            request = json.loads(body)
        except ValueError:
            return
        if not isinstance(request, dict) or request.get("method") != "tools/call":
            return
        name = str((request.get("params") or {}).get("name", ""))
        parsed = naming.split(name)
        await self.audit.record(user_id=user_id, channel="api", server_slug=parsed[0] if parsed else None,
                                tool=name, timer=timer, outcome="denied", error=reason)
