"""Shared pieces for the REST API: sessions, the current user, role checks, CSRF.

FastAPI "dependencies" are functions a route declares as parameters; FastAPI
runs them before the route body. `current_user` and `require_admin` are how
roles are enforced on the server, regardless of what the dashboard shows.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import User, UserSession
from ..security import hash_token, new_token

SESSION_COOKIE = "gw_session"


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def settings(request: Request) -> Settings:
    return request.app.state.settings


async def db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


DB = Annotated[AsyncSession, Depends(db)]


# --- sessions -----------------------------------------------------------------

async def start_session(session: AsyncSession, response: Response, cfg: Settings, user_id: int) -> None:
    """Create a server-side session and hand the browser its token as a cookie."""
    token, token_hash = new_token()
    now = datetime.now(timezone.utc)
    await session.execute(delete(UserSession).where(UserSession.user_id == user_id, UserSession.expires_at < now))
    session.add(UserSession(user_id=user_id, token_hash=token_hash,
                            expires_at=now + timedelta(hours=cfg.session_hours)))
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=int(cfg.session_hours * 3600),
        httponly=True,          # JavaScript can't read it (limits XSS damage)
        samesite="strict",      # never sent on cross-site requests (CSRF)
        secure=cfg.cookie_secure,
        path="/",
    )


def end_session_cookie(response: Response, cfg: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=cfg.cookie_secure)


async def current_user(request: Request, session: DB) -> CurrentUser:
    """The logged-in user, re-read from the DB on every request, so logout,
    disabling and role changes apply immediately."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = (await session.execute(
            select(User).join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.token_hash == hash_token(token),
                   UserSession.expires_at > datetime.now(timezone.utc))
        )).scalar_one_or_none()
        if row is not None and row.status == "active":
            return CurrentUser(row.id, row.email, row.role)
    raise HTTPException(401, "not logged in")


def require_admin(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, "admin only")
    return user


UserDep = Annotated[CurrentUser, Depends(current_user)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]


# --- CSRF ---------------------------------------------------------------------

def check_origin(request: Request) -> None:
    """Second CSRF layer (the first is SameSite=Strict): a state-changing
    request that carries an Origin header must come from our own dashboard.
    Browsers always send Origin on POST/PATCH/DELETE; tools like curl don't,
    and they can't carry a victim's cookie anyway."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = urlsplit(request.app.state.settings.public_url)
    given = urlsplit(origin)
    if (given.scheme, given.netloc) != (allowed.scheme, allowed.netloc):
        raise HTTPException(403, "cross-origin request refused")


# --- login rate limiting ------------------------------------------------------

class LoginLimiter:
    """At most `max_failures` failed logins per email per `window` seconds.
    In memory: fine for one gateway replica (multi-replica would need Redis)."""

    def __init__(self, max_failures: int = 5, window: float = 15 * 60):
        self.max_failures, self.window = max_failures, window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q, cutoff = self._failures[key], time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, email: str) -> bool:
        return len(self._prune(email.lower())) >= self.max_failures

    def failed(self, email: str) -> None:
        self._prune(email.lower()).append(time.monotonic())

    def succeeded(self, email: str) -> None:
        self._failures.pop(email.lower(), None)
