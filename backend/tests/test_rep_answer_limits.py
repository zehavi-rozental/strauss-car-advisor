"""The rep's answers and the rate limits, with real questions and a simulated clock (QA 22/10).

(a) An answer refused by the limit is never lost or saved twice: the question stays open with nothing saved,
    the same answer goes through once the window moves, and sending it again is a 409.
(b) A venue: several reps on one network (one address). The screens' polling never uses up the answers, and
    each rep (the screen's X-Rep-Session, made at login) has their own answer budget, under a ceiling for the
    address that a code holder cannot pass by inventing session ids.
"""
from __future__ import annotations

import pytest

from app.api.ratelimit import RepGate

from .conftest import make_client

TOKEN = "answer-limit-test-token"
NOA = ["מנהלת מוצר, שכר בסיס 18,500",
       "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
       'יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש"ח בחודש',
       "אטו 3",
       "אני רוצה שנציג התחבורה יאשר את ההשתתפות"]          # the rule since 22/10: the rep only when asked


def rep(session: str | None = None) -> dict[str, str]:
    return {"X-Rep-Token": TOKEN, **({"X-Rep-Session": session} if session else {})}


REP_A, REP_B = "a" * 8 + "-1111-2222-3333", "b" * 8 + "-4444-5555-6666"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def venue(engine):
    client = make_client(engine, rep_access_token=TOKEN, create_rate_limit_per_minute=500)
    clock = Clock()
    s = client.app.state.ctx.settings
    client.app.state.ctx.rep_gate = RepGate(s.rep_read_limit_per_minute, s.rep_write_limit_per_minute,
                                            s.rep_write_address_limit_per_minute, s.rep_auth_failure_limit_per_minute,
                                            clock=clock)
    return client, clock


def open_questions(client, n: int) -> list[dict]:
    """n employees waiting for the rep (one question each)."""
    before = {q["id"] for q in client.get("/api/rep/questions?status=OPEN", headers=rep()).json()}
    for _ in range(n):
        client.cookies.clear()
        cid = client.post("/api/conversations").json()["conversation_id"]
        for text in NOA:
            client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    qs = [q for q in client.get("/api/rep/questions?status=OPEN", headers=rep()).json() if q["id"] not in before]
    assert len(qs) == n
    return qs


def answer(client, q: dict, session: str | None = None):
    return client.post(f"/api/rep/questions/{q['id']}/answer", json={"value": "300"}, headers=rep(session))


def status(client, q: dict) -> dict:
    return client.get(f"/api/rep/questions/{q['id']}", headers=rep()).json()


# ---------------------------------------------------------------- (a) nothing lost, nothing saved twice

def test_the_sixteenth_answer_in_a_minute_waits_and_is_saved_once(venue):
    client, clock = venue
    qs = open_questions(client, 16)
    clock.now += 60                                      # the setup's reads are out of the window
    codes = [answer(client, q, REP_A).status_code for q in qs]
    assert codes == [200] * 15 + [429]
    refused = qs[15]
    r = status(client, refused)
    assert (r["status"], r["answer_value"]) == ("OPEN", None)          # nothing saved, nothing half-saved
    clock.now += 60
    assert answer(client, refused, REP_A).json()["status"] == "ANSWERED"
    again = answer(client, refused, REP_A)
    assert again.status_code == 409 and again.json()["detail"] == "השאלה כבר נענתה"
    answered = client.get("/api/rep/questions?status=ANSWERED", headers=rep()).json()
    ids = [q["id"] for q in answered]
    assert len(ids) == len(set(ids)) and set(ids) >= {q["id"] for q in qs}


def test_a_refused_answer_has_retry_after_and_says_when(venue):
    client, clock = venue
    qs = open_questions(client, 16)
    clock.now += 60
    for q in qs[:15]:
        answer(client, q, REP_A)
    r = answer(client, qs[15], REP_A)
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60
    assert "נסו שוב בעוד" in r.json()["detail"]


# ---------------------------------------------------------------- (b) a venue: one address, several reps

def test_polling_never_uses_up_the_answers(venue):
    client, clock = venue
    qs = open_questions(client, 10)
    for _ in range(200):                                 # many screens polling on the same network
        client.get("/api/rep/questions?status=OPEN", headers=rep(REP_A))
    assert [answer(client, q, REP_A).status_code for q in qs] == [200] * 10


def test_two_reps_on_one_network_each_have_their_own_answers(venue):
    client, clock = venue
    qs = open_questions(client, 30)
    clock.now += 60
    a = [answer(client, q, REP_A).status_code for q in qs[:15]]
    b = [answer(client, q, REP_B).status_code for q in qs[15:]]
    assert a == [200] * 15 and b == [200] * 15           # before: one budget of 15 for both


def test_four_reps_fit_and_the_address_has_a_ceiling(venue):
    # a code holder inventing session ids cannot pass the address's ceiling (the old total of 60)
    client, clock = venue
    sessions = [f"{i:08d}-rotating-session" for i in range(10)]
    codes = []
    for i in range(70):
        codes.append(client.post("/api/rep/questions/00000000-0000-0000-0000-000000000000/answer",
                                 json={"value": "1"}, headers=rep(sessions[i // 7])).status_code)
    assert codes.count(404) == 60 and codes.count(429) == 10    # 404: admitted, no such question


def test_without_a_session_the_answers_are_counted_for_the_address(venue):
    # an older screen or a script: the old behaviour, 15 a minute for the address
    client, clock = venue
    codes = [client.post("/api/rep/questions/00000000-0000-0000-0000-000000000000/answer",
                         json={"value": "1"}, headers=rep(None)).status_code for _ in range(16)]
    assert codes == [404] * 15 + [429]


@pytest.mark.parametrize("bad", ["short", "x" * 65, "has spaces in it at all", "<script>alert(1)</script>xx"])
def test_a_malformed_session_id_is_ignored(venue, bad):
    client, clock = venue
    for _ in range(15):
        client.post("/api/rep/questions/00000000-0000-0000-0000-000000000000/answer",
                    json={"value": "1"}, headers=rep(None))
    r = client.post("/api/rep/questions/00000000-0000-0000-0000-000000000000/answer",
                    json={"value": "1"}, headers=rep(bad))
    assert r.status_code == 429                          # counted for the address, like no session at all


def test_a_session_id_never_helps_guessing_the_code(venue):
    client, clock = venue
    wrong = [client.get("/api/rep/questions", headers={"X-Rep-Token": "guess", "X-Rep-Session": f"{i:016d}"})
             .status_code for i in range(11)]
    assert wrong == [401] * 10 + [429]                   # counted for the address, whatever the session id
    assert client.get("/api/rep/questions", headers=rep(REP_A)).status_code == 429


def test_a_wrong_code_never_creates_a_session_budget(venue):
    client, clock = venue
    for i in range(10):
        client.post("/api/rep/questions/x/answer", json={"value": "1"},
                    headers={"X-Rep-Token": "guess", "X-Rep-Session": f"{i:016d}"})
    assert client.app.state.ctx.rep_gate.writes._hits == {}            # only authorized answers are counted
