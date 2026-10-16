"""Upstream MCP servers: everyone can view; admins add, edit, enable/disable, remove, test.

Every change is committed and then `registry.refresh()` is awaited before the
response is sent, so the tool list (for every MCP client and the chat agent)
reflects the change immediately, not on the next health tick.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .. import naming
from ..models import UpstreamServer
from ..ssrf import UnsafeUrlError
from .deps import DB, AdminDep, UserDep
from .schemas import ServerCreate, ServerOut, ServerTest, ServerUpdate, ToolOut

router = APIRouter(prefix="/servers", tags=["servers"])

HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")
# Headers the gateway itself must control; an upstream credential can't override them.
RESERVED_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection",
                    "accept", "cookie", "mcp-session-id", "mcp-protocol-version"}


def _server_out(request: Request, row: UpstreamServer) -> ServerOut:
    tools = request.app.state.registry.tools_for(row.slug)
    return ServerOut(
        slug=row.slug, name=row.name, url=row.url, auth_type=row.auth_type, header_name=row.header_name,
        credential_set=row.credential_encrypted is not None, enabled=row.enabled, status=row.status,
        last_error=row.last_error, last_checked_at=row.last_checked_at,
        tools=[ToolOut(name=t.name, description=t.description) for t in tools],
    )


async def _get(session, slug: str) -> UpstreamServer:
    row = await session.scalar(select(UpstreamServer).where(UpstreamServer.slug == slug))
    if row is None:
        raise HTTPException(404, f"no server {slug!r}")
    return row


def _validate_auth(auth_type: str, header_name: str | None, has_credential: bool) -> None:
    if auth_type == "header":
        if not header_name or not HEADER_NAME_RE.match(header_name):
            raise HTTPException(422, "header auth needs a header name (letters, digits, '-')")
        if header_name.lower() in RESERVED_HEADERS:
            raise HTTPException(422, f"{header_name!r} is reserved")
    if auth_type != "none" and not has_credential:
        raise HTTPException(422, f"{auth_type} auth needs a credential")


async def _check_url(request: Request, url: str) -> None:
    try:
        await request.app.state.check_url(url)
    except UnsafeUrlError as exc:
        raise HTTPException(400, f"URL refused: {exc}")


async def _apply(request: Request, session, slug: str) -> ServerOut:
    await session.commit()
    await request.app.state.registry.refresh()      # immediate: tools appear/disappear now
    # refresh() wrote the new status through its own DB session; drop this
    # session's cached copy of the row so we return the fresh status.
    session.expire_all()
    return _server_out(request, await _get(session, slug))


@router.get("")
async def list_servers(_: UserDep, request: Request, session: DB) -> list[ServerOut]:
    rows = (await session.scalars(select(UpstreamServer).order_by(UpstreamServer.id))).all()
    return [_server_out(request, r) for r in rows]


@router.post("", status_code=201)
async def create_server(body: ServerCreate, _: AdminDep, request: Request, session: DB) -> ServerOut:
    if not naming.valid_slug(body.slug):
        raise HTTPException(422, "slug: lowercase letters, digits and '-', not starting/ending with '-'")
    if await session.scalar(select(UpstreamServer.id).where(UpstreamServer.slug == body.slug)):
        raise HTTPException(409, f"slug {body.slug!r} is already used")
    _validate_auth(body.auth_type, body.header_name, bool(body.credential))
    await _check_url(request, body.url)
    box = request.app.state.box
    session.add(UpstreamServer(
        slug=body.slug, name=body.name, url=body.url, auth_type=body.auth_type,
        header_name=body.header_name if body.auth_type == "header" else None,
        credential_encrypted=box.encrypt(body.credential) if body.auth_type != "none" else None,
        enabled=body.enabled, status="unknown",
    ))
    return await _apply(request, session, body.slug)


@router.patch("/{slug}")
async def update_server(slug: str, body: ServerUpdate, _: AdminDep, request: Request, session: DB) -> ServerOut:
    row = await _get(session, slug)
    fields = body.model_fields_set           # which fields the client actually sent
    if "name" in fields and body.name is not None:
        row.name = body.name
    if "url" in fields and body.url is not None and body.url != row.url:
        await _check_url(request, body.url)
        row.url = body.url
    if "auth_type" in fields and body.auth_type is not None:
        row.auth_type = body.auth_type
    if "header_name" in fields:
        row.header_name = body.header_name
    if "credential" in fields:                # omitted = keep; null = clear; value = replace
        row.credential_encrypted = request.app.state.box.encrypt(body.credential) if body.credential else None
    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled
    if row.auth_type != "header":
        row.header_name = None
    if row.auth_type == "none":
        row.credential_encrypted = None
    _validate_auth(row.auth_type, row.header_name, row.credential_encrypted is not None)
    return await _apply(request, session, slug)


@router.delete("/{slug}", status_code=204)
async def delete_server(slug: str, _: AdminDep, request: Request, session: DB) -> None:
    await session.delete(await _get(session, slug))     # audit rows keep the slug as plain text
    await session.commit()
    await request.app.state.registry.refresh()


@router.post("/{slug}/test")
async def test_server(slug: str, _: AdminDep, request: Request, session: DB) -> ServerTest:
    await _get(session, slug)
    await request.app.state.registry.refresh()          # a fresh health check, right now
    status, last_error = request.app.state.registry.statuses().get(slug, ("unknown", None))
    return ServerTest(slug=slug, status=status, last_error=last_error,
                      tool_count=len(request.app.state.registry.tools_for(slug)))
