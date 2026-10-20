"""Deployment entry point (app/api/serve_spa.py): the API and the built React app from one origin."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.serve_spa import create_render_app
from app.modules.conversation.public import LLMChain
from app.shared.settings import Settings

from .conftest import build_engine

INDEX = "<!doctype html><html><body><div id='root'>strauss drive</div></body></html>"


def make(tmp_path, *, dist: bool = True, **overrides) -> TestClient:
    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text(INDEX, encoding="utf-8")
    (dist_dir / "assets" / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    settings = Settings(_env_file=None, llm_provider="none", **overrides)
    return TestClient(create_render_app(settings, engine=build_engine(), llm=LLMChain(),
                                        dist_dir=dist_dir if dist else None))


def test_the_root_and_the_client_side_routes_serve_the_app(tmp_path):
    client = make(tmp_path)
    for path in ("/", "/employee", "/rep", "/rep/anything/deep"):
        r = client.get(path)
        assert r.status_code == 200 and "strauss drive" in r.text, path       # refresh / shared link works


def test_built_assets_are_served_as_files(tmp_path):
    r = make(tmp_path).get("/assets/app-abc123.js")
    assert r.status_code == 200 and "console.log" in r.text and "strauss drive" not in r.text


def test_the_api_is_never_shadowed_by_the_static_files(tmp_path):
    client = make(tmp_path)
    health = client.get("/api/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    created = client.post("/api/conversations")
    assert created.status_code == 201 and created.json()["conversation_id"]


@pytest.mark.parametrize("path", ["/api/nope", "/api/conversations/x/nope", "/api"])
def test_an_unknown_api_path_is_a_404_not_an_html_page(tmp_path, path):
    r = make(tmp_path).get(path)
    assert r.status_code == 404 and "strauss drive" not in r.text


def test_the_session_cookie_is_first_party_and_secure_over_https(tmp_path):
    client = make(tmp_path, session_cookie_secure=True)
    r = client.post("/api/conversations")
    cookie = r.headers["set-cookie"].lower()
    assert "sca_session=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "domain=" not in cookie                       # host-only: same origin as the page


def test_the_rep_api_stays_closed_without_a_token(tmp_path):
    client = make(tmp_path)                              # REP_ACCESS_TOKEN empty (the default)
    assert client.get("/api/rep/questions").status_code in (401, 403, 404, 503)
    assert client.get("/api/rep/questions", headers={"X-Rep-Token": "x"}).status_code in (401, 403, 404, 503)


def test_the_rep_api_opens_only_with_the_configured_token(tmp_path):
    client = make(tmp_path, rep_access_token="demo-token-for-tests")
    assert client.get("/api/rep/questions", headers={"X-Rep-Token": "wrong"}).status_code == 401
    assert client.get("/api/rep/questions", headers={"X-Rep-Token": "demo-token-for-tests"}).status_code == 200


def test_the_payslip_upload_is_closed_in_the_demo(tmp_path):
    client = make(tmp_path)                              # PAYSLIP_ENABLED defaults to false, as in render.yaml
    r = client.post("/api/payslip/extract", files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code in (403, 404, 503)


def test_without_a_frontend_dir_it_is_the_plain_api(tmp_path, monkeypatch):
    monkeypatch.delenv("FRONTEND_DIST_DIR", raising=False)
    client = make(tmp_path, dist=False)
    assert client.get("/api/health").status_code == 200
    assert client.get("/employee").status_code == 404


def test_a_frontend_dir_without_index_html_stops_the_start(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="index.html"):
        create_render_app(Settings(_env_file=None, llm_provider="none"), engine=build_engine(), llm=LLMChain(),
                          dist_dir=tmp_path / "empty")
