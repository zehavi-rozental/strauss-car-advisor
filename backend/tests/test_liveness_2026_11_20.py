"""Liveness, 20/11 (Stage A of the Neon-consumption fix).

GET /api/live says only that the process answers. It must never open a database connection, so a container health
check on it neither wakes a sleeping Neon compute nor marks a working app "unhealthy" while the database is away
(the heal cron would then restart it). GET /api/health stays the readiness check: it asks the database and says
db:false / "degraded" when it cannot reach it.

What these tests prove, and what they do not:
- no connection attempt, pool checkout, SQL statement or session reaches the database from /api/live (SQLite here;
  the PostgreSQL test below adds the driver's connect() and the server's own view, pg_stat_activity);
- /api/live keeps answering when the database becomes unavailable after startup, and /api/health reports it;
- "it does not wake Neon" is an inference from "nothing reaches the server": no local or CI test runs against Neon.
- a startup with the database down from the first moment still fails (pinned below): that is a later stage.
"""
from __future__ import annotations

import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, exc, text

from app.api.main import create_app
from app.api.routes import router
from app.modules.conversation.public import LLMChain
from app.shared.db import Base, make_engine
from app.shared.settings import Settings

from .conftest import build_engine, make_client

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "oracle" / "docker-compose.yml"
HEALTHCHECK_CODE = ("import urllib.request as u; "
                    "u.urlopen('http://127.0.0.1:10000/api/live', timeout=4)")


class Outage:
    """A database that can go away after the app started: every new connection attempt fails while `down`."""

    def __init__(self, engine):
        self.engine = engine
        self.down = False
        self.attempts = 0
        event.listen(engine, "do_connect", self._connect)

    def _connect(self, dialect, conn_rec, cargs, cparams):
        self.attempts += 1
        if self.down:
            raise sqlite3.OperationalError("database unavailable (test outage)")
        return None                                     # the normal connect

    def start(self) -> None:
        self.down = True
        self.engine.dispose()                           # pooled connections go: the next use must connect again

    def end(self) -> None:
        self.down = False


class Counter:
    """Everything that would reach a database: connection attempts, new connections, checkouts, statements."""

    def __init__(self, engine):
        self.counts = {"do_connect": 0, "connect": 0, "checkout": 0, "statement": 0}
        event.listen(engine, "do_connect", lambda *a: self._hit("do_connect"))
        event.listen(engine, "connect", lambda *a: self._hit("connect"))
        event.listen(engine, "checkout", lambda *a: self._hit("checkout"))
        event.listen(engine, "before_cursor_execute", lambda *a: self._hit("statement"))

    def _hit(self, name: str) -> None:
        self.counts[name] += 1

    def total(self) -> int:
        return sum(self.counts.values())


def file_engine(tmp_path):
    """A file SQLite database with a real connection pool (the in-memory one keeps a single static connection, so it
    could not show a new connection being refused)."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine


# ---------- L1 / L7: the database goes away after startup, and comes back ----------

def test_live_answers_and_health_reports_the_outage_when_the_database_goes_away(tmp_path):
    engine = file_engine(tmp_path)
    outage = Outage(engine)
    client = make_client(engine)
    assert client.get("/api/health").json()["db"] is True           # up at first

    outage.start()
    live = client.get("/api/live")
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    health = client.get("/api/health")
    assert health.status_code == 200                                 # unchanged contract: 200, with db:false
    assert health.json()["db"] is False and health.json()["status"] == "degraded"
    assert outage.attempts >= 1                                      # health really tried, and was refused

    outage.end()                                                     # L7: back without a restart
    assert client.get("/api/health").json()["db"] is True
    assert client.get("/api/health").json()["status"] == "ok"


# ---------- L2: nothing reaches the database ----------

def test_live_never_connects_checks_out_or_runs_sql(tmp_path):
    engine = file_engine(tmp_path)
    client = make_client(engine)
    engine.dispose()                                                 # an empty pool: any use would connect
    counter = Counter(engine)
    for _ in range(200):                                             # also: no rate limit on it
        r = client.get("/api/live")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert counter.counts == {"do_connect": 0, "connect": 0, "checkout": 0, "statement": 0}

    client.get("/api/health")                                        # contrast: the readiness check does reach it
    assert counter.counts["checkout"] >= 1 and counter.counts["statement"] >= 1


# ---------- L3 / L4: no app context, session or service ----------

class _Sentinel:
    def __getattr__(self, name):
        raise AssertionError(f"/api/live touched the app context ({name})")


def test_live_touches_no_app_context_session_or_service(engine):
    client = make_client(engine)
    client.app.state.ctx = _Sentinel()                               # any context, session or service use raises
    r = client.get("/api/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_live_opens_no_session(engine):
    client = make_client(engine)
    ctx = client.app.state.ctx
    real, opened = ctx.sessions, []

    def counting():
        opened.append(1)
        return real()

    ctx.sessions = counting
    for _ in range(100):
        assert client.get("/api/live").status_code == 200
    assert opened == []
    client.get("/api/health")                                        # contrast
    assert opened == [1]


def test_live_needs_nothing_but_the_router():
    app = FastAPI()                                                  # no app.state.ctx at all
    app.include_router(router)
    r = TestClient(app).get("/api/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# ---------- L8: with the full lifespan (startup and shutdown run) ----------

def test_live_within_the_full_lifespan(tmp_path):
    engine = file_engine(tmp_path)
    outage = Outage(engine)
    with make_client(engine) as client:
        outage.start()
        assert client.get("/api/live").json() == {"status": "ok"}


# ---------- L11: pinned, a startup with the database down still fails (a later stage handles it) ----------

def test_a_startup_with_the_database_down_still_fails(tmp_path):
    engine = file_engine(tmp_path)
    Outage(engine).start()
    settings = Settings(_env_file=None, llm_provider="none", email_backend="disabled")
    with pytest.raises(exc.OperationalError):
        create_app(settings, engine=engine, llm=LLMChain())


# ---------- L5: PostgreSQL, the driver and the server's own view ----------

PG_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="needs TEST_DATABASE_URL (PostgreSQL), as in CI")
def test_live_reaches_no_postgres_backend(monkeypatch):
    import psycopg2

    observer = build_engine()                                        # fresh tables on the CI database
    name = "strauss-live-test"
    engine = make_engine(PG_URL, connect_args={"application_name": name})
    client = make_client(engine)                                     # its startup used one pooled connection
    counter = Counter(engine)
    connects = []
    real_connect = psycopg2.connect
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: connects.append(1) or real_connect(*a, **k))

    def backends() -> int:
        with observer.connect() as c:
            return c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :n"),
                             {"n": name}).scalar_one()

    before = backends()
    for _ in range(100):
        assert client.get("/api/live").json() == {"status": "ok"}
    assert counter.total() == 0 and connects == []
    assert backends() == before                                      # the server saw no new connection

    assert client.get("/api/health").json()["db"] is True            # contrast: the readiness check uses the pool
    assert counter.counts["checkout"] >= 1
    engine.dispose()
    observer.dispose()


# ---------- L9: the container health check calls /api/live only ----------

def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_container_health_check_is_liveness_only():
    services = _compose()["services"]
    check = services["app"]["healthcheck"]
    test = check["test"]
    assert test[:3] == ["CMD", "python", "-c"] and test[3] == HEALTHCHECK_CODE
    joined = " ".join(test)
    assert "/api/live" in joined and "/api/health" not in joined and "db" not in joined
    assert (check["interval"], check["timeout"], check["retries"], check["start_period"]) == ("30s", "6s", 3, "240s")
    assert services["caddy"]["depends_on"] == {"app": {"condition": "service_healthy"}}


# ---------- L6: the exact health-check command, against a real server ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_check(port: int) -> int:
    code = _compose()["services"]["app"]["healthcheck"]["test"][3]
    assert code == HEALTHCHECK_CODE                                  # the command under test is the deployed one
    code = re.sub(r"127\.0\.0\.1:10000", f"127.0.0.1:{port}", code)  # only the port differs
    return subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30).returncode


def test_the_health_check_command_passes_against_a_real_server_with_the_database_down(tmp_path):
    engine = file_engine(tmp_path)
    outage = Outage(engine)
    app = make_client(engine).app
    outage.start()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:   # waiting for the server, not for the result
            time.sleep(0.05)
        assert server.started
        assert _run_check(port) == 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_the_health_check_command_fails_when_nothing_answers():
    assert _run_check(_free_port()) != 0
