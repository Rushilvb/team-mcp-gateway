"""Team members (admin only): invite, list, change role, enable/disable."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, func, select

from ..models import ApiKey, User, UserSession
from ..security import new_token
from .deps import DB, AdminDep
from .schemas import UserCreate, UserInvited, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])
INVITE_DAYS = 7


async def _user_out(session, user: User) -> UserOut:
    active_keys = await session.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)))
    return UserOut(id=user.id, email=user.email, role=user.role, status=user.status,
                   created_at=user.created_at, active_keys=active_keys or 0)


def _issue_invite(user: User, public_url: str) -> str:
    token, token_hash = new_token()
    user.invite_token_hash = token_hash
    user.invite_expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)
    return f"{public_url}/invite/{token}"


@router.get("")
async def list_users(_: AdminDep, session: DB) -> list[UserOut]:
    users = (await session.scalars(select(User).order_by(User.id))).all()
    return [await _user_out(session, u) for u in users]


@router.post("", status_code=201)
async def invite_user(body: UserCreate, _: AdminDep, request: Request, session: DB) -> UserInvited:
    if await session.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(409, f"{body.email} already exists")
    user = User(email=body.email, role=body.role, status="invited")
    url = _issue_invite(user, request.app.state.settings.public_url)
    session.add(user)
    await session.commit()
    return UserInvited(user=await _user_out(session, user), invite_url=url)


@router.post("/{user_id}/invite")
async def reissue_invite(user_id: int, _: AdminDep, request: Request, session: DB) -> UserInvited:
    """New link for a lost/expired invite (the old link stops working)."""
    user = await session.get(User, user_id)
    if user is None or user.status != "invited":
        raise HTTPException(404, "no pending invite for this user")
    url = _issue_invite(user, request.app.state.settings.public_url)
    await session.commit()
    return UserInvited(user=await _user_out(session, user), invite_url=url)


@router.patch("/{user_id}")
async def update_user(user_id: int, body: UserUpdate, _: AdminDep, session: DB) -> UserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    if body.status == "active" and user.status == "invited":
        raise HTTPException(400, "an invited user becomes active by accepting the invite")

    loses_admin = user.role == "admin" and user.status == "active" and (
        body.role == "member" or body.status == "disabled")
    if loses_admin:
        active_admins = await session.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.status == "active"))
        if active_admins <= 1:
            raise HTTPException(409, "cannot demote or disable the last active admin")

    if body.role is not None:
        user.role = body.role
    if body.status is not None:
        user.status = body.status
        if body.status == "disabled":
            # Log them out everywhere. Their API keys stop working too, because
            # MCP auth checks the user's status on every request.
            await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    return await _user_out(session, user)
