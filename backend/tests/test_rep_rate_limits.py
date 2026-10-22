"""The representative API's rate limits (api/ratelimit.RepGate), with a simulated clock.

QA 22/10: after ~7 actions the rep screen showed "יותר מדי בקשות". One limit of 60 a minute per address was
shared by the screen's own polling (two lists every 3 s: 40 a minute) and the rep's answers (an answer and a
refresh of both lists: 3). Now the screen polls one list every 5 s (RepPage, repPolling.ts), and the server
gives reads, answers and wrong codes their own caps inside the same total.
"""
from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.ratelimit import RepGate

from .conftest import BACKEND, make_client

TOKEN = "rate-limit-test-token"
AUTH = {"X-Rep-Token": TOKEN}
WRONG = {"X-Rep-Token": "a-guess"}
MISSING = "/api/rep/questions/00000000-0000-0000-0000-000000000000/answer"   # an answer: a write (404 here)

# the screen as RepPage polls now (repPolling.ts): the list on screen every 5 s, the other one every 60 s;
# an answer is the POST and a refresh of both lists
POLL_S, OTHER_S = 5, 60


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rep(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    clock = Clock()
    s = client.app.state.ctx.settings
    client.app.state.ctx.rep_gate = RepGate(s.rep_read_limit_per_minute, s.rep_write_limit_per_minute,
                                            s.rep_write_address_limit_per_minute, s.rep_auth_failure_limit_per_minute,
                                            clock=clock)
    return client, clock


def read(client, status="OPEN", headers=AUTH) -> int:
    return client.get(f"/api/rep/questions?status={status}", headers=headers).status_code


def answer(client, headers=AUTH) -> int:
    return client.post(MISSING, json={"value": "300"}, headers=headers).status_code


def screen_minute(client, clock, answers_at=(), tabs=1) -> list[int]:
    """One minute of the rep screen, second by second, as RepPage drives it now. Returns every status."""
    codes = []
    start = clock.now
    for second in range(60):
        clock.now = start + second
        for _ in range(tabs):
            if second % POLL_S == 0:
                codes.append(read(client, "OPEN"))
            if second % OTHER_S == 0:
                codes.append(read(client, "ANSWERED"))
        if second in answers_at:
            codes += [answer(client), read(client, "OPEN"), read(client, "ANSWERED")]
    clock.now = start + 60
    return codes


# ---------------------------------------------------------------- the original bug

def test_seven_actions_in_a_minute_are_never_refused(rep):
    # the QA report: about 7 actions in a minute with the screen open
    client, clock = rep
    assert 429 not in screen_minute(client, clock, answers_at=(5, 12, 20, 28, 36, 44, 52))


# ---------------------------------------------------------------- normal work is never refused

def test_ten_minutes_of_normal_work(rep):
    client, clock = rep
    codes = []
    for minute in range(10):
        codes += screen_minute(client, clock, answers_at=(10, 40) if minute % 2 else (25,))
    assert 429 not in codes


def test_ten_answers_in_a_row_with_the_screen_open(rep):
    client, clock = rep
    assert 429 not in screen_minute(client, clock, answers_at=tuple(range(2, 52, 5)))


def test_three_tabs_for_five_minutes(rep):
    client, clock = rep
    codes = []
    for _ in range(5):
        codes += screen_minute(client, clock, tabs=3)
    assert 429 not in codes


def test_three_tabs_and_answers_in_one_of_them(rep):
    # reads may be paused for a few seconds (the screen waits Retry-After), the answers never are
    client, clock = rep
    start = clock.now
    answers = []
    for second in range(60):
        clock.now = start + second
        for _ in range(3):
            if second % POLL_S == 0:
                read(client, "OPEN")
            if second == 0:
                read(client, "ANSWERED")
        if second in range(3, 53, 5):
            answers.append(answer(client))
            read(client, "OPEN")
            read(client, "ANSWERED")
    assert 429 not in answers


# ---------------------------------------------------------------- abuse is still refused

def test_a_flood_of_reads_is_refused_with_retry_after(rep):
    client, clock = rep
    codes = [read(client) for _ in range(200)]
    assert codes.count(200) == 45 and codes.count(429) == 155
    r = client.get("/api/rep/questions", headers=AUTH)
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60
    assert "נסו שוב בעוד" in r.json()["detail"]
    clock.now += 60
    assert read(client) == 200                               # the window moved on


def test_a_flood_of_answers_is_refused(rep):
    client, clock = rep
    codes = [answer(client) for _ in range(100)]
    assert codes.count(404) == 15 and codes.count(429) == 85


def test_mixing_kinds_never_passes_the_total(rep):
    client, clock = rep
    admitted = 0
    for _ in range(150):
        admitted += read(client) != 429
        admitted += answer(client) != 429
    assert admitted == 60                                    # 45 reads (the address) + 15 answers (this session)


def test_refused_requests_do_not_use_up_another_kind(rep):
    client, clock = rep
    for _ in range(1000):
        read(client)                                         # all but 45 refused, and not counted anywhere
    assert answer(client) == 404


def test_retry_after_counts_down_with_the_window(rep):
    client, clock = rep
    for _ in range(45):
        read(client)
    clock.now += 40
    r = client.get("/api/rep/questions", headers=AUTH)
    assert r.status_code == 429 and r.headers["Retry-After"] == "20"


# ---------------------------------------------------------------- wrong access codes

def test_the_eleventh_wrong_code_in_a_minute_is_refused_and_locks_the_address(rep):
    client, clock = rep
    assert [read(client, headers=WRONG) for _ in range(10)] == [401] * 10
    assert read(client, headers=WRONG) == 429
    assert read(client) == 429                               # not even the right code until the window moves
    clock.now += 60
    assert read(client) == 200


def test_wrong_codes_are_counted_on_answers_too(rep):
    client, clock = rep
    assert [answer(client, headers=WRONG) for _ in range(10)] == [401] * 10
    assert read(client, headers=WRONG) == 429


def test_a_missing_code_counts_as_a_wrong_one(rep):
    client, clock = rep
    for _ in range(10):
        assert client.get("/api/rep/questions").status_code == 401
    assert client.get("/api/rep/questions").status_code == 429


def test_the_limits_are_per_address(rep):
    client, clock = rep
    for _ in range(11):
        read(client, headers=WRONG)
    other = TestClient(client.app, client=("198.51.100.20", 4000))
    assert other.get("/api/rep/questions", headers=AUTH).status_code == 200


# ---------------------------------------------------------------- the address behind Render's proxy

def _trusted() -> str:
    spec = importlib.util.spec_from_file_location("render_start", BACKEND / "deploy" / "render_start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)      # main() is not called on import
    return module.TRUSTED_PROXIES


def test_behind_the_proxy_a_forged_address_does_not_escape_the_limit(rep):
    # Render's edge (a private address) appends the real client; whatever the caller typed on the left is ignored
    client, clock = rep
    proxied = TestClient(ProxyHeadersMiddleware(client.app, trusted_hosts=_trusted()), client=("10.1.2.3", 5000))
    codes = [proxied.get("/api/rep/questions", headers={**WRONG, "X-Forwarded-For": f"6.6.6.{i}, 203.0.113.7"})
             .status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # another real client behind the same proxy is not locked out
    other = proxied.get("/api/rep/questions", headers={**AUTH, "X-Forwarded-For": "203.0.113.8"})
    assert other.status_code == 200


def test_a_public_caller_cannot_choose_its_address(rep):
    client, clock = rep
    direct = TestClient(ProxyHeadersMiddleware(client.app, trusted_hosts=_trusted()), client=("198.51.100.9", 5000))
    codes = [direct.get("/api/rep/questions", headers={**WRONG, "X-Forwarded-For": f"6.6.6.{i}"}).status_code
             for i in range(11)]
    assert codes[-1] == 429                                  # every guess counted on 198.51.100.9
