"""SSRF guard for upstream URLs.

Admins register upstream URLs, and the gateway then makes requests to them
from inside the cluster. Without a guard, a URL could point the gateway at the
cloud metadata service (169.254.169.254, which hands out cloud credentials),
at itself (localhost), or at internal services (databases, the Kubernetes
API) that were never meant to be reachable.

Rules:
  1. Only http/https, no "user:pass@" in the URL, a host is required.
  2. Cluster-internal names (no dot, or *.svc / *.cluster.local) are allowed
     only if they are in SSRF_ALLOWED_HOSTS.
  3. The host is resolved and EVERY address is checked: loopback, link-local
     (includes cloud metadata), unspecified and multicast are always refused;
     private/reserved ranges (which include pod and Service IPs) are refused
     unless the hostname is allowlisted.
The check runs at registration and again before every new upstream
connection, so a DNS answer that changes later (DNS rebinding) is caught too.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection
from urllib.parse import urlsplit

IP = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[IP]]]
CLUSTER_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local")


class UnsafeUrlError(ValueError):
    pass


async def _resolve(host: str, port: int) -> list[IP]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def _is_cluster_name(host: str) -> bool:
    return "." not in host or host.endswith(CLUSTER_SUFFIXES)


async def check_upstream_url(url: str, allowlist: Collection[str], resolve: Resolver = _resolve) -> None:
    """Raise UnsafeUrlError if the gateway must not connect to `url`."""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError(f"invalid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError("only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials in the URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeUrlError("URL has no host")

    allowlisted = host in allowlist
    try:
        addresses: list[IP] = [ipaddress.ip_address(host)]       # literal IP
    except ValueError:
        if _is_cluster_name(host) and not allowlisted:
            raise UnsafeUrlError(f"cluster-internal host {host!r} is not in the allowlist")
        try:
            addresses = await resolve(host, port)
        except OSError as exc:
            raise UnsafeUrlError(f"cannot resolve {host!r}") from exc

    for address in addresses:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped     # ::ffff:127.0.0.1 is still loopback
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            raise UnsafeUrlError(f"{host!r} resolves to a forbidden address ({address})")
        if not allowlisted and (address.is_private or address.is_reserved):
            raise UnsafeUrlError(f"{host!r} resolves to an internal address ({address}); add the host to the allowlist if intended")
