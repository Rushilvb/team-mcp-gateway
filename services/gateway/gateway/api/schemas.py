"""Request/response shapes of the REST API (Pydantic validates input and
defines exactly which fields leave the server: no credential or hash ever does)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Role = Literal["admin", "member"]
AuthType = Literal["none", "bearer", "header"]


class Me(BaseModel):
    id: int
    email: str
    role: Role


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class InviteInfo(BaseModel):
    email: str


class AcceptInviteIn(BaseModel):
    password: str = Field(min_length=10, max_length=200, description="At least 10 characters.")


class UserOut(BaseModel):
    id: int
    email: str
    role: Role
    status: Literal["active", "invited", "disabled"]
    created_at: datetime
    active_keys: int


class UserCreate(BaseModel):
    email: EmailStr
    role: Role = "member"


class UserInvited(BaseModel):
    user: UserOut
    invite_url: str          # shown once; only its hash is stored


class UserUpdate(BaseModel):
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None


class KeyOut(BaseModel):
    id: int
    user_id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreate(BaseModel):
    name: str = Field(default="", max_length=100)


class KeyCreated(BaseModel):
    key: str                 # the full API key, shown exactly once
    item: KeyOut


class ToolOut(BaseModel):
    name: str
    description: str | None


class ServerOut(BaseModel):
    slug: str
    name: str
    url: str
    auth_type: AuthType
    header_name: str | None
    credential_set: bool     # never the credential itself
    enabled: bool
    status: str
    last_error: str | None
    last_checked_at: datetime | None
    tools: list[ToolOut]


class ServerCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=32, description="Tool prefix: lowercase letters, digits, '-'.")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    auth_type: AuthType = "none"
    header_name: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4000)
    enabled: bool = True


class ServerUpdate(BaseModel):
    """Every field optional. For `credential`: omitted = keep, null = clear, value = replace."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    auth_type: AuthType | None = None
    header_name: str | None = Field(default=None, max_length=100)
    credential: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None


class ServerTest(BaseModel):
    slug: str
    status: str
    last_error: str | None
    tool_count: int


class AuditOut(BaseModel):
    id: int
    user_id: int | None
    user_email: str | None
    channel: str
    server_slug: str | None
    tool: str
    started_at: datetime
    duration_ms: int
    outcome: str
    error_message: str | None


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
    limit: int
    offset: int
