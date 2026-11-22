"""app.readiness (20/11): a deployment passes only when the running app says db true AND status "ok".

Deterministic: a stub HTTP server answers what each test scripts; the clock and the pause are injected (no real
waiting); the timeout case holds the answer on an Event until the client has given up.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
import yaml
from hypothesis import given, settings, strategies as st

from app import readiness
from app.readiness import BAD_ANSWER, CHECK_ERROR, NOT_READY, READY, Result, classify, health_url, main, probe

from .conftest import make_client
from .test_liveness_2026_11_20 import Outage, file_engine

REPO = Path(__file__).resolve().parents[2]
OK = json.dumps({"status": "ok", "db": True, "rules_version": "x"}).encode()
DOWN = json.dumps({"status": "degraded", "db": False}).encode()


# ---------- a scripted stub server ----------

class Stub:
    """Answers each GET with the next scripted reply (the last one repeats). A reply is (status, body, headers) or
    "hold": wait until `release` is set, then answer OK (for the timeout case)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.hits = 0
        self.release = threading.Event()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                stub.hits += 1
                reply = stub.replies.pop(0) if len(stub.replies) > 1 else stub.replies[0]
                if reply == "hold":
                    stub.release.wait(10)
                    reply = (200, OK, {})
                status, body, headers = reply
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass                               # the client gave up first (timeout case)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/health"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    made = []

    def make(*replies):
        s = Stub(replies)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


class FakeTime:
    """An injected clock: sleep() moves it, nothing really waits."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds


def run(url, *, deadline_s=30.0, pause_s=5.0, timeout_s=2.0, probe_fn=probe):
    t, lines = FakeTime(), []
    code = main(url=url, deadline_s=deadline_s, pause_s=pause_s, timeout_s=timeout_s, probe=probe_fn,
                clock=t.clock, sleep=t.sleep, out=lines.append)
    return code, lines, t


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- R1-R3: ready, never ready, ready after a while ----------

def test_r1_ready(stub):
    s = stub((200, OK, {}))
    code, lines, t = run(s.url)
    assert code == READY and lines == ["PASS  database ready (the app reports db true, status ok)"] and t.sleeps == 0


def test_r2_database_never_comes_back_fails_at_the_deadline(stub):
    s = stub((200, DOWN, {}))
    code, lines, t = run(s.url, deadline_s=30.0, pause_s=5.0)
    assert code == NOT_READY and lines[-1].startswith("FAIL  database not ready after 30 s")
    assert 5 <= s.hits <= 7 and t.sleeps == s.hits - 1              # retried, on the injected clock only


def test_r3_ready_after_the_database_wakes(stub):
    s = stub((200, DOWN, {}), (200, DOWN, {}), (200, OK, {}))
    code, lines, t = run(s.url)
    assert code == READY and s.hits == 3 and t.sleeps == 2


# ---------- R4-R9, R13, R21: permanent bad answers fail at once ----------

PERMANENT = {
    "not json": (200, b"<html>oops</html>", {}),
    "not utf-8": (200, b"\xff\xfe\x00", {}),
    "a list": (200, b"[]", {}),
    "a string": (200, b'"ok"', {}),
    "null": (200, b"null", {}),
    "db missing": (200, b'{"status": "ok"}', {}),
    "status missing": (200, b'{"db": true}', {}),
    "db as a string": (200, b'{"db": "true", "status": "ok"}', {}),
    "db as 1": (200, b'{"db": 1, "status": "ok"}', {}),
    "db as yes": (200, b'{"db": "yes", "status": "ok"}', {}),
    "unknown status": (200, b'{"db": true, "status": "fine"}', {}),
    "db true but degraded": (200, b'{"db": true, "status": "degraded"}', {}),
    "db false but ok": (200, b'{"db": false, "status": "ok"}', {}),
    "http 500 with a ready body": (500, OK, {}),
    "http 404": (404, b'{"detail": "Not Found"}', {}),
    "http 401": (401, OK, {}),
    "too large": (200, b'{"db": true, "status": "ok", "pad": "' + b"x" * (70 * 1024) + b'"}', {}),
}


@pytest.mark.parametrize("name", PERMANENT)
def test_a_permanent_bad_answer_fails_at_once_without_waiting(stub, name):
    s = stub(PERMANENT[name])
    code, lines, t = run(s.url, deadline_s=300.0)
    assert code == BAD_ANSWER, name
    assert s.hits == 1 and t.sleeps == 0, name                      # R19: one probe, no pause
    assert lines[-1].startswith("FAIL  readiness: ") and lines[-1].endswith("(not retried)")


# ---------- R10: a redirect is never followed ----------

def test_r10_a_redirect_is_not_followed(stub):
    target = stub((200, OK, {}))
    s = stub((302, b"", {"Location": target.url}))
    code, lines, _ = run(s.url)
    assert code == BAD_ANSWER and "HTTP 302" in lines[-1] and target.hits == 0


# ---------- transient HTTP answers are retried ----------

@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_or_unavailable_answer_is_retried(stub, status):
    s = stub((status, b"", {}), (200, OK, {}))
    code, _, t = run(s.url)
    assert code == READY and s.hits == 2 and t.sleeps == 1


# ---------- R20: transient, then permanent: stops at the permanent one ----------

def test_r20_transient_then_permanent_stops_at_once(stub):
    s = stub((200, DOWN, {}), (404, b"", {}))
    code, _, t = run(s.url, deadline_s=300.0)
    assert code == BAD_ANSWER and s.hits == 2 and t.sleeps == 1


# ---------- R11 / R12: no answer at all ----------

def test_r11_nothing_listening_is_retried_then_fails():
    url = f"http://127.0.0.1:{_free_port()}/api/health"
    code, lines, t = run(url, deadline_s=10.0, pause_s=5.0)
    assert code == NOT_READY and "no answer" in lines[-1] and t.sleeps >= 1


def test_r12_a_timeout_is_transient(stub):
    s = stub("hold")
    result = probe(s.url, 0.3)                      # the stub holds its answer until released: a sure timeout
    s.release.set()
    assert result.kind == "transient" and result.reason.startswith("no answer (")
    s2 = stub("hold")
    code, lines, _ = run(s2.url, deadline_s=0.0, timeout_s=0.3)
    s2.release.set()
    assert code == NOT_READY and "no answer" in lines[-1]


# ---------- R14: a proxy from the environment is ignored ----------

def test_r14_an_environment_proxy_is_ignored(stub, monkeypatch):
    dead = f"http://127.0.0.1:{_free_port()}"
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, dead)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    s = stub((200, OK, {}))
    assert run(s.url)[0] == READY


# ---------- R15: nothing from the answer leaks into the output ----------

def test_r15_no_value_from_the_answer_is_printed(stub):
    s = stub((200, json.dumps({"db": "s3cr3t-value", "status": "hunter2", "password": "pw-123",
                               "database_url": "postgresql://u:p@h/db"}).encode(), {"X-Token": "tok-456"}))
    code, lines, _ = run(s.url)
    out = "\n".join(lines)
    assert code == BAD_ANSWER
    for leaked in ("s3cr3t", "hunter2", "pw-123", "postgresql", "tok-456", "password", "database_url"):
        assert leaked not in out


# ---------- R16: the check itself breaking is a failure ----------

def test_r16_an_unexpected_error_is_a_failure():
    def broken(url, timeout):
        raise RuntimeError("boom with a secret value")

    code, lines, _ = run("http://127.0.0.1:1/api/health", probe_fn=broken)
    assert code == CHECK_ERROR and lines == ["FAIL  readiness check error (RuntimeError)"]


def test_an_unknown_result_kind_is_never_success():
    code, _, _ = run("http://127.0.0.1:1/api/health", probe_fn=lambda u, t: Result("READY", "odd"))
    assert code == BAD_ANSWER


# ---------- R18: success if and only if db is True and status == "ok" ----------

_values = st.one_of(st.none(), st.booleans(), st.integers(-2, 2), st.floats(allow_nan=False),
                    st.sampled_from(["ok", "degraded", "true", "false", "OK", "", "yes"]), st.lists(st.booleans()))


@settings(max_examples=300, deadline=None)
@given(db=_values, status=_values, extra=st.dictionaries(st.text(max_size=5), _values, max_size=3))
def test_r18_ready_only_when_db_is_true_and_status_is_ok(db, status, extra):
    body = {**extra, "db": db, "status": status}
    result = classify(200, json.dumps(body).encode())
    assert (result.kind == "ready") == (db is True and status == "ok")


@settings(max_examples=100, deadline=None)
@given(code=st.integers(100, 599))
def test_r18_no_status_code_but_200_is_ever_ready(code):
    result = classify(code, OK)
    assert (result.kind == "ready") == (code == 200)


# ---------- the address and port inside the container ----------

def test_the_address_is_the_app_inside_the_container():
    assert health_url({}) == "http://127.0.0.1:10000/api/health"     # render_start's default port
    assert health_url({"PORT": "10001"}) == "http://127.0.0.1:10001/api/health"
    for bad in ("abc", "0", "70000", "10000; rm -rf /", ""):
        with pytest.raises(ValueError):
            health_url({"PORT": bad})


def test_the_port_matches_how_the_container_starts_the_app():
    compose = yaml.safe_load((REPO / "deploy" / "oracle" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["app"]["environment"]["PORT"] == readiness.DEFAULT_PORT
    assert re.search(r"PORT=10000\b", (REPO / "Dockerfile").read_text(encoding="utf-8"))
    start = (REPO / "backend" / "deploy" / "render_start.py").read_text(encoding="utf-8")
    assert 'os.environ.get("PORT", "10000")' in start


def test_a_bad_port_is_a_check_error_not_a_success(monkeypatch):
    monkeypatch.setenv("PORT", "not-a-port")
    lines = []
    assert main(out=lines.append) == CHECK_ERROR and lines == ["FAIL  readiness check error (ValueError)"]


# ---------- R17: the real app, database up and down ----------

def test_r17_the_real_app_ready_and_then_not_ready(tmp_path):
    engine = file_engine(tmp_path)
    outage = Outage(engine)
    app = make_client(engine).app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:   # waiting for the server, not for the result
            time.sleep(0.05)
        assert server.started
        url = f"http://127.0.0.1:{port}/api/health"
        assert run(url)[0] == READY
        outage.start()
        code, lines, _ = run(url, deadline_s=0.0)
        assert code == NOT_READY and "db false" in lines[-1]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
