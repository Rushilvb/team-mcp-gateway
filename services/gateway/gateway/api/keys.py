"""API keys: everyone manages their own; admins can also see and revoke anyone's."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..models import ApiKey, User
from ..security import new_api_key
from .deps import DB, AdminDep, UserDep
from .schemas import KeyCreate, KeyCreated, KeyOut

router = APIRouter(tags=["keys"])


def _key_out(k: ApiKey) -> KeyOut:
    return KeyOut(id=k.id, user_id=k.user_id, name=k.name, prefix=k.prefix, created_at=k.created_at,
                  last_used_at=k.last_used_at, revoked_at=k.revoked_at)


async def _keys_of(session, user_id: int) -> list[KeyOut]:
    keys = (await session.scalars(select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.id.desc()))).all()
    return [_key_out(k) for k in keys]


@router.get("/me/keys")
async def my_keys(user: UserDep, session: DB) -> list[KeyOut]:
    return await _keys_of(session, user.id)


@router.post("/me/keys", status_code=201)
async def create_key(body: KeyCreate, user: UserDep, session: DB) -> KeyCreated:
    key, prefix, key_hash = new_api_key()
    row = ApiKey(user_id=user.id, name=body.name, prefix=prefix, key_hash=key_hash)
    session.add(row)
    await session.commit()
    return KeyCreated(key=key, item=_key_out(row))      # the only time the full key is ever returned


@router.get("/users/{user_id}/keys")
async def user_keys(user_id: int, _: AdminDep, session: DB) -> list[KeyOut]:
    if await session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    return await _keys_of(session, user_id)


@router.post("/users/{user_id}/keys", status_code=201)
async def issue_key(user_id: int, body: KeyCreate, admin: AdminDep, session: DB) -> KeyCreated:
    """Admin creates a key on a member's behalf (e.g. for a teammate who can't log in yet).
    The key belongs to the member: their calls are audited under their name."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    if user.status != "active":
        raise HTTPException(400, f"user is {user.status}; their keys would not work")
    key, prefix, key_hash = new_api_key()
    row = ApiKey(user_id=user.id, name=body.name or f"issued by {admin.email}"[:100], prefix=prefix, key_hash=key_hash)
    session.add(row)
    await session.commit()
    return KeyCreated(key=key, item=_key_out(row))      # shown once, to the admin, to hand over


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: int, user: UserDep, session: DB) -> None:
    key = await session.get(ApiKey, key_id)
    # Someone else's key looks exactly like a missing one to a member:
    # no hint that the ID exists.
    if key is None or (key.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)    # the next MCP request with it gets 401
        await session.commit()
