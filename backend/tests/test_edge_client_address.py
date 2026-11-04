"""The visitor's address behind the Cloudflare edge Worker (api/ratelimit.client_address, edge/src/worker.js).

Behind the Worker every request reaches Render from a few Cloudflare addresses, so every per-address limit
(new conversations, payslips, email returns, the rep screen's wrong codes) would be shared by all visitors. The
Worker sends the visitor's address with a shared secret; the server uses it only when the secret matches.
A direct caller, or a caller who forges the header, keeps today's address.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.ratelimit import client_address

from .conftest import make_client

SECRET = "edge-test-secret-0123456789abcdef"
TOKEN = "edge-rep-token"


def _request(headers: dict[str, str], client: str = "10.0.0.5") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "client": (client, 1234),
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def test_the_edge_address_is_used_only_with_the_matching_secret():
    edge = {"X-Edge-Secret": SECRET, "X-Edge-Client-IP": "203.0.113.7"}
    assert client_address(_request(edge), SECRET) == "203.0.113.7"
    assert client_address(_request(edge), "") == "10.0.0.5"                       # not configured: ignored
    assert client_address(_request({**edge, "X-Edge-Secret": "a-guess"}), SECRET) == "10.0.0.5"
    assert client_address(_request({"X-Edge-Client-IP": "203.0.113.7"}), SECRET) == "10.0.0.5"
    assert client_address(_request({**edge, "X-Edge-Client-IP": "not-an-ip"}), SECRET) == "10.0.0.5"
    assert client_address(_request({**edge, "X-Edge-Client-IP": " 2001:db8::1 "}), SECRET) == "2001:db8::/64"


def test_visitors_behind_the_edge_get_their_own_limits(engine):
    client = make_client(engine, edge_proxy_secret=SECRET, create_rate_limit_per_minute=2)

    def create(ip: str, secret: str = SECRET) -> int:
        return client.post("/api/conversations",
                           headers={"X-Edge-Secret": secret, "X-Edge-Client-IP": ip}).status_code

    assert [create("203.0.113.1") for _ in range(3)] == [201, 201, 429]
    assert create("203.0.113.2") == 201                  # another visitor, same Cloudflare address: not blocked


def test_a_forged_edge_header_counts_for_the_real_address(engine):
    client = make_client(engine, edge_proxy_secret=SECRET, create_rate_limit_per_minute=2)
    codes = [client.post("/api/conversations",
                         headers={"X-Edge-Secret": "a-guess", "X-Edge-Client-IP": f"198.51.100.{i}"}).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]                      # every attempt counted on the connection's address


def test_a_direct_caller_behind_render_cannot_choose_its_address(engine):
    # straight to Render (no Worker): the caller writes both headers, without the secret; Render's proxy (a
    # private address) appends the real client. The rep screen's wrong-code lockout must hit that real client.
    app = make_client(engine, edge_proxy_secret=SECRET, rep_access_token=TOKEN).app
    direct = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="10.0.0.0/8"), client=("10.1.2.3", 5000))
    codes = [direct.get("/api/rep/questions", headers={
        "X-Rep-Token": "a-guess", "X-Edge-Client-IP": f"6.6.6.{i}",
        "X-Forwarded-For": f"6.6.6.{i}, 198.51.100.9"}).status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
