"""First-start setup: the admin user and the default upstream servers.

Idempotent and non-destructive: things are only created if missing, so edits an
admin makes later (in the dashboard) are never overwritten by a restart.
"""

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import naming
from .config import Settings
from .models import UpstreamServer, User
from .security import CredentialBox, hash_password
from .upstreams import UrlCheck

log = logging.getLogger(__name__)


async def bootstrap(sm: async_sessionmaker, settings: Settings, box: CredentialBox, check_url: UrlCheck) -> None:
    async with sm() as session:
        if settings.admin_email and settings.admin_password:
            exists = await session.scalar(select(User.id).where(User.email == settings.admin_email))
            if not exists:
                session.add(User(email=settings.admin_email, role="admin", status="active",
                                 password_hash=hash_password(settings.admin_password)))
                log.info("bootstrap: created admin %s", settings.admin_email)

        for spec in settings.bootstrap_upstreams:      # list order = registration order (woo first)
            slug = spec["slug"]
            if await session.scalar(select(UpstreamServer.id).where(UpstreamServer.slug == slug)):
                continue
            if not naming.valid_slug(slug):
                raise ValueError(f"bootstrap upstream: invalid slug {slug!r}")
            await check_url(spec["url"])
            auth_type = spec.get("auth_type", "none")
            credential = None
            if auth_type != "none":
                credential = os.environ.get(spec["credential_env"])
                if not credential:
                    raise ValueError(f"bootstrap upstream {slug!r}: env var {spec['credential_env']} is empty")
            session.add(UpstreamServer(
                slug=slug, name=spec["name"], url=spec["url"], auth_type=auth_type,
                header_name=spec.get("header_name"), enabled=True, status="unknown",
                credential_encrypted=box.encrypt(credential) if credential else None,
            ))
            await session.flush()            # keep insertion order = id order
            log.info("bootstrap: registered upstream %s (%s)", slug, auth_type)
        await session.commit()
