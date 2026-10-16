"""Audit log with filters and pagination. Members only ever see their own rows."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..models import AuditLog, User
from .deps import DB, UserDep
from .schemas import AuditOut, AuditPage

router = APIRouter(tags=["audit"])


@router.get("/audit")
async def audit(
    user: UserDep,
    session: DB,
    user_id: int | None = None,
    server: str | None = None,
    outcome: Literal["success", "error", "timeout", "denied"] | None = None,
    channel: Literal["api", "chat"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    if not user.is_admin:
        user_id = user.id          # enforced here, whatever the client asked for
    filters = []
    if user_id is not None:
        filters.append(AuditLog.user_id == user_id)
    if server:
        filters.append(AuditLog.server_slug == server)
    if outcome:
        filters.append(AuditLog.outcome == outcome)
    if channel:
        filters.append(AuditLog.channel == channel)
    if since:
        filters.append(AuditLog.started_at >= since)
    if until:
        filters.append(AuditLog.started_at < until)

    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    rows = (await session.execute(
        select(AuditLog, User.email).outerjoin(User, AuditLog.user_id == User.id)
        .where(*filters).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    )).all()
    items = [AuditOut(id=a.id, user_id=a.user_id, user_email=email, channel=a.channel, server_slug=a.server_slug,
                      tool=a.tool, started_at=a.started_at, duration_ms=a.duration_ms, outcome=a.outcome,
                      error_message=a.error_message) for a, email in rows]
    return AuditPage(items=items, total=total or 0, limit=limit, offset=offset)
