"""Minimal async client for the WooCommerce REST API v3.

Why REST and not WP-CLI: REST is WooCommerce's stable, versioned contract; it
needs no shell access to (or exec into) the WordPress pod; and the API key is
bound to a low-privilege shop_manager user, so this server can do exactly what
that user can and nothing more.
"""

from typing import Any
from urllib.parse import urlsplit

import httpx

from . import oauth
from .config import Settings

API_PREFIX = "/wp-json/wc/v3"


class WooError(Exception):
    """A failed store call, with a message that is safe to show the model."""

    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class WooClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        # The signed URL uses the scheme we actually connect with (WooCommerce
        # checks is_ssl()) but the store's PUBLIC host (it uses its home URL).
        scheme = urlsplit(settings.woo_base_url).scheme
        self._signed_base = f"{scheme}://{settings.public_host}"
        self._http = httpx.AsyncClient(
            base_url=settings.woo_base_url,
            # WordPress may redirect requests whose Host doesn't match its site
            # URL. We send the right Host and treat any redirect as an error
            # rather than following it somewhere unexpected.
            headers={"Host": settings.public_host},
            follow_redirects=False,
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        path = API_PREFIX + path
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}
        signed = oauth.sign(method, self._signed_base + path, query,
                            self._settings.consumer_key, self._settings.consumer_secret)
        try:
            response = await self._http.request(method, path, params=signed, json=json)
        except httpx.TimeoutException:
            raise WooError(None, f"store unreachable: timed out after {self._settings.timeout_seconds:g}s")
        except httpx.HTTPError as exc:
            raise WooError(None, f"store unreachable: {exc.__class__.__name__}: {exc}")

        if response.is_redirect:
            raise WooError(response.status_code,
                           f"store redirected to {response.headers.get('location')!r}; "
                           "check that WOO_PUBLIC_HOST matches the WordPress site URL")
        if response.status_code >= 400:
            raise WooError(response.status_code, _error_message(response))
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


def _error_message(response: httpx.Response) -> str:
    # WooCommerce errors look like {"code": "...", "message": "...", "data": {"status": 404}}
    try:
        message = response.json().get("message")
    except ValueError:
        message = None
    return message or f"HTTP {response.status_code}: {response.text[:200]}"
