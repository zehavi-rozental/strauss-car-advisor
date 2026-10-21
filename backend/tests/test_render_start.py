"""The Render start script trusts X-Forwarded-For only from private proxies, so the per-IP rate limits key on
the address Render's edge appended, not on one the caller typed."""
from __future__ import annotations

import asyncio
import importlib.util

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .conftest import BACKEND


def _trusted() -> str:
    spec = importlib.util.spec_from_file_location("render_start", BACKEND / "deploy" / "render_start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)      # main() is not called on import
    return module.TRUSTED_PROXIES


def _client_ip(peer: str, forwarded: str) -> str:
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=_trusted())
    scope = {"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]}
    asyncio.run(middleware(scope, None, None))
    return seen["client"]


def test_forged_leftmost_entry_is_ignored():
    # the caller sent "X-Forwarded-For: 6.6.6.6"; Render's edge appended the real address 203.0.113.7
    assert _client_ip("10.1.2.3", "6.6.6.6, 203.0.113.7") == "203.0.113.7"


def test_header_from_a_public_peer_is_not_trusted():
    assert _client_ip("198.51.100.9", "6.6.6.6") == "198.51.100.9"


def test_never_trust_everyone():
    assert "*" not in _trusted()
