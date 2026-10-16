"""Gateway application: FastAPI app hosting the /mcp endpoint and the /api REST API.

Run: uvicorn gateway.app:create_app --factory --host 0.0.0.0 --port 8000
"""

import contextlib
import functools
import logging

import anyio
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from .api import router as api_router
from .api.deps import LoginLimiter
from .audit import Audit
from .auth import Authenticator, McpAuth
from .bootstrap import bootstrap
from .config import Settings
from .db import make_engine, make_sessionmaker, wait_for_db
from .mcp_server import build_mcp_server
from .security import CredentialBox
from .ssrf import check_upstream_url
from .upstreams import Registry, UrlCheck

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx2").setLevel(logging.WARNING)   # otherwise one line per health-check request


def create_app(settings: Settings | None = None, check_url: UrlCheck | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # Tests inject a permissive URL check so fake upstreams can run on localhost.
    check_url = check_url or functools.partial(check_upstream_url, allowlist=settings.ssrf_allowed_hosts)

    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    box = CredentialBox(settings.fernet_key)
    audit = Audit(sm)
    registry = Registry(sm, box, check_url,
                        call_timeout=settings.call_timeout_seconds,
                        health_timeout=settings.health_timeout_seconds,
                        health_interval=settings.health_interval_seconds)
    mcp = build_mcp_server(registry, audit)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=False,    # SSE allowed: needed for subscriptions/listen (list_changed)
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=[f"http://{h}" for h in settings.mcp_allowed_hosts]
                            + [f"https://{h}" for h in settings.mcp_allowed_hosts],
        ),
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await wait_for_db(engine)
        await bootstrap(sm, settings, box, check_url)
        await registry.refresh()                    # first health check before serving
        async with anyio.create_task_group() as tg:
            tg.start_soon(registry.run_forever)     # then every health_interval seconds
            async with mcp.session_manager.run():   # the MCP SDK's own lifecycle
                yield
            tg.cancel_scope.cancel()
        await registry.close()
        await engine.dispose()

    app = FastAPI(title="Team MCP Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Shared objects the REST API routes reach through request.app.state.
    app.state.settings, app.state.sessionmaker, app.state.registry, app.state.audit = settings, sm, registry, audit
    app.state.box, app.state.check_url, app.state.login_limiter = box, check_url, LoginLimiter()
    app.include_router(api_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> str:
        return "ok"

    # Put the SDK's /mcp route directly on our app, wrapped in our auth.
    # (Mounting the SDK's whole sub-app under /mcp would make POST /mcp
    # redirect to /mcp/, which MCP clients don't follow.)
    sdk_endpoint = next(r.endpoint for r in mcp_app.routes if getattr(r, "path", None) == "/mcp")
    app.router.routes.append(Route("/mcp", endpoint=McpAuth(sdk_endpoint, Authenticator(sm), audit)))
    return app
