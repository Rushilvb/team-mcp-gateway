"""Static-credential check in front of the MCP endpoint.

A pure ASGI wrapper (not Starlette's BaseHTTPMiddleware, which can interfere
with streamed responses). Every HTTP request except the open paths must carry
the expected header value, compared in constant time so response timing reveals
nothing about the secret.

This file is identical in notes-mcp and fx-mcp. Each service has its own Docker
build context, so a shared package would complicate builds for ~40 lines.
"""

import hmac
import json


class RequireHeader:
    def __init__(self, app, header: str, secret: str, *, scheme: str | None = None,
                 open_paths: tuple[str, ...] = ("/healthz",)):
        """header: e.g. "authorization" or "x-api-key".
        scheme: e.g. "Bearer" -> expects "Authorization: Bearer <secret>"."""
        if not secret:
            raise ValueError(f"empty secret for header {header!r}")
        self.app = app
        self.header = header.lower().encode()
        self.secret = secret.encode()
        self.scheme = scheme
        self.open_paths = open_paths

    def _authorized(self, value: bytes) -> bool:
        if self.scheme:
            # "Bearer <token>": the scheme name is case-insensitive (RFC 7235).
            given_scheme, _, value = value.partition(b" ")
            if given_scheme.lower() != self.scheme.lower().encode():
                return False
        return hmac.compare_digest(value, self.secret)

    async def __call__(self, scope, receive, send):
        # Lifespan events and open paths (health checks) pass straight through.
        if scope["type"] != "http" or scope["path"] in self.open_paths:
            return await self.app(scope, receive, send)

        value = dict(scope["headers"]).get(self.header, b"")
        if self._authorized(value):
            return await self.app(scope, receive, send)

        name = self.header.decode()
        body = json.dumps({"error": "unauthorized", "message": f"missing or invalid {name} header"}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if self.scheme:
            headers.append((b"www-authenticate", self.scheme.encode()))
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
