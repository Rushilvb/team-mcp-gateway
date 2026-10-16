"""Admin CLI, used via `kubectl exec` until the dashboard exists (Phase 5).

  python -m gateway.cli create-user --email b@example.com [--role member|admin]
  python -m gateway.cli create-key --email b@example.com [--name laptop]   # prints the key ONCE
  python -m gateway.cli list-keys [--email b@example.com]
  python -m gateway.cli revoke-key --prefix urk_Ab3dE6gH
  python -m gateway.cli upstreams
  python -m gateway.cli set-upstream --slug fx --disable | --enable
  python -m gateway.cli audit [--limit 20]
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select, update

from .config import database_url_from_env
from .db import make_engine, make_sessionmaker
from .models import ApiKey, AuditLog, UpstreamServer, User
from .security import new_api_key


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def fmt(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


async def main(args: argparse.Namespace) -> None:
    engine = make_engine(database_url_from_env())
    sm = make_sessionmaker(engine)
    try:
        async with sm() as s:
            if args.cmd == "create-user":
                if await s.scalar(select(User.id).where(User.email == args.email)):
                    fail(f"user {args.email} already exists")
                s.add(User(email=args.email, role=args.role, status="active"))
                await s.commit()
                print(f"created {args.role} {args.email}")

            elif args.cmd == "create-key":
                user = await s.scalar(select(User).where(User.email == args.email))
                if user is None:
                    fail(f"no user {args.email}")
                key, prefix, key_hash = new_api_key()
                s.add(ApiKey(user_id=user.id, name=args.name, prefix=prefix, key_hash=key_hash))
                await s.commit()
                print(f"API key for {args.email} (shown once, store it now):\n{key}\nprefix: {prefix}")

            elif args.cmd == "list-keys":
                q = select(ApiKey, User.email).join(User).order_by(ApiKey.id)
                if args.email:
                    q = q.where(User.email == args.email)
                print(f"{'PREFIX':14} {'USER':28} {'NAME':12} {'CREATED':20} {'LAST USED':20} REVOKED")
                for key, email in (await s.execute(q)).all():
                    print(f"{key.prefix:14} {email:28} {key.name:12} {fmt(key.created_at):20} "
                          f"{fmt(key.last_used_at):20} {fmt(key.revoked_at)}")

            elif args.cmd == "revoke-key":
                result = await s.execute(update(ApiKey).where(ApiKey.prefix == args.prefix, ApiKey.revoked_at.is_(None))
                                         .values(revoked_at=datetime.now(timezone.utc)))
                await s.commit()
                print(f"revoked {result.rowcount} key(s) with prefix {args.prefix}")

            elif args.cmd == "upstreams":
                print(f"{'SLUG':8} {'ENABLED':8} {'STATUS':12} {'AUTH':7} {'CHECKED':20} URL / LAST ERROR")
                for u in (await s.scalars(select(UpstreamServer).order_by(UpstreamServer.id))).all():
                    print(f"{u.slug:8} {str(u.enabled):8} {u.status:12} {u.auth_type:7} {fmt(u.last_checked_at):20} "
                          f"{u.url}" + (f"\n{'':58}last error: {u.last_error}" if u.last_error else ""))

            elif args.cmd == "set-upstream":
                result = await s.execute(update(UpstreamServer).where(UpstreamServer.slug == args.slug)
                                         .values(enabled=args.enable))
                await s.commit()
                if not result.rowcount:
                    fail(f"no upstream {args.slug}")
                print(f"{args.slug}: {'enabled' if args.enable else 'disabled'} (applies within one health interval)")

            elif args.cmd == "audit":
                rows = (await s.execute(select(AuditLog, User.email).outerjoin(User)
                                        .order_by(AuditLog.id.desc()).limit(args.limit))).all()
                print(f"{'TIME (UTC)':20} {'USER':24} {'CHAN':5} {'TOOL':28} {'OUTCOME':8} {'MS':>6}  ERROR")
                for a, email in reversed(rows):
                    print(f"{fmt(a.started_at):20} {(email or '-'):24} {a.channel:5} {a.tool[:28]:28} "
                          f"{a.outcome:8} {a.duration_ms:>6}  {(a.error_message or '')[:80]}")
    finally:
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gateway.cli", description="Team MCP gateway admin CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user"); c.add_argument("--email", required=True)
    c.add_argument("--role", choices=["member", "admin"], default="member")
    c = sub.add_parser("create-key"); c.add_argument("--email", required=True); c.add_argument("--name", default="cli")
    c = sub.add_parser("list-keys"); c.add_argument("--email")
    c = sub.add_parser("revoke-key"); c.add_argument("--prefix", required=True)
    sub.add_parser("upstreams")
    c = sub.add_parser("set-upstream"); c.add_argument("--slug", required=True)
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--enable", dest="enable", action="store_true"); g.add_argument("--disable", dest="enable", action="store_false")
    c = sub.add_parser("audit"); c.add_argument("--limit", type=int, default=20)
    return p


if __name__ == "__main__":
    asyncio.run(main(parser().parse_args()))
