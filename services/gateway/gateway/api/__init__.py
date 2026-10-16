"""REST API for the dashboard, mounted at /api. Every route passes the CSRF
Origin check; roles are enforced per route through deps.UserDep / deps.AdminDep."""

from fastapi import APIRouter, Depends

from . import audit, keys, login, servers, users
from .deps import check_origin

router = APIRouter(prefix="/api", dependencies=[Depends(check_origin)])
for module in (login, users, keys, servers, audit):
    router.include_router(module.router)
