"""Login, logout, current user, and accepting an invite link."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete, select

from ..models import User, UserSession
from ..security import DUMMY_PASSWORD_HASH, hash_password, hash_token, verify_password
from .deps import DB, SESSION_COOKIE, UserDep, end_session_cookie, start_session
from .schemas import AcceptInviteIn, InviteInfo, LoginIn, Me

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, session: DB) -> Me:
    limiter = request.app.state.login_limiter
    if limiter.blocked(body.email):
        raise HTTPException(429, "too many failed attempts; try again in 15 minutes")
    user = await session.scalar(select(User).where(User.email == body.email))
    # Always run one argon2 verification, even for unknown emails, so the
    # response time doesn't reveal whether the account exists.
    ok = verify_password(user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH, body.password)
    if not (ok and user and user.status == "active"):
        limiter.failed(body.email)
        raise HTTPException(401, "invalid email or password")
    limiter.succeeded(body.email)
    await start_session(session, response, request.app.state.settings, user.id)
    return Me(id=user.id, email=user.email, role=user.role)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, session: DB) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:   # delete server-side: the old cookie value is now worthless
        await session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        await session.commit()
    end_session_cookie(response, request.app.state.settings)


@router.get("/auth/me")
async def me(user: UserDep) -> Me:
    return Me(id=user.id, email=user.email, role=user.role)


async def _invited_user(session, token: str) -> User:
    user = await session.scalar(select(User).where(User.invite_token_hash == hash_token(token)))
    if user is None or user.status != "invited" or user.invite_expires_at is None \
            or user.invite_expires_at < datetime.now(timezone.utc):
        raise HTTPException(404, "this invite link is invalid or has expired; ask an admin for a new one")
    return user


@router.get("/invites/{token}")
async def invite_info(token: str, session: DB) -> InviteInfo:
    return InviteInfo(email=(await _invited_user(session, token)).email)


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, body: AcceptInviteIn, request: Request, response: Response, session: DB) -> Me:
    user = await _invited_user(session, token)
    user.password_hash = hash_password(body.password)
    user.status = "active"
    user.invite_token_hash = None          # single use
    user.invite_expires_at = None
    await session.commit()
    await start_session(session, response, request.app.state.settings, user.id)
    return Me(id=user.id, email=user.email, role=user.role)
