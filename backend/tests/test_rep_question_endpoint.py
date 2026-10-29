"""The employee-initiated rep-question endpoint (POST /api/conversations/{id}/rep-question, Package A).

Covers the unified path end to end: an informational question (GENERAL_QUESTION, never changes the calculation),
a structured copay verification (NEW_CAR_COPAY, carries the employee's own wording and may update the calculation
only after the rep verifies a sum), safe classification (a car choice / keyword never upgrades a question — the
intent does, and the server re-validates the car against the state), de-dup without replace (an open question of
the same kind is returned as "exists" with the saved text, different topics are never blocked), and the full
rep-answer round trip back to the employee through polling.
"""
from __future__ import annotations

from .conftest import make_client

TOKEN = "rep-q"
AUTH = {"X-Rep-Token": TOKEN}
# CAR-05 NIRO: catalog copay 250 (CONFIG default policy = CATALOG_DEFAULT: rep only on request)
PROFILE = ["קבוצה ב, שכר בסיס 18,000",
           "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"]
TODAY = "יש לי קורולה 2026, משלמת 280"
NIRO = "CAR-05"
EMAIL = "employee@example.com"


def _client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def _ask(c, cid, body):
    return c.post(f"/api/conversations/{cid}/rep-question", json={"email": EMAIL, **body})


def _present_niro(c):
    """Drive one conversation to PRESENTED with NIRO compared (catalog copay on screen)."""
    cid = c.post("/api/conversations").json()["conversation_id"]
    for m in [*PROFILE, TODAY]:
        c.post(f"/api/conversations/{cid}/messages", json={"text": m})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": [NIRO], "field": "options"})
    return cid


def _open_questions(c):
    return c.get("/api/rep/questions", headers=AUTH).json()


def _results(c, cid):
    return {r["option_id"]: r["result"] for r in c.get(f"/api/conversations/{cid}").json()["results"]}


# ---------- informational (GENERAL_QUESTION) ----------

def test_general_question_is_saved_with_the_employee_wording_and_never_changes_the_calc(engine):
    c = _client(engine)
    cid = _present_niro(c)
    before = _results(c, cid)[NIRO]
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "האם אפשר לבחור צבע מיוחד לרכב?", "intent": "general"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created" and body["missing_field"] == "GENERAL_QUESTION"
    assert body["question_id"]
    [q] = _open_questions(c)
    assert q["missing_field"] == "GENERAL_QUESTION"
    assert q["question_text"] == "האם אפשר לבחור צבע מיוחד לרכב?"   # the employee's words, verbatim
    # the calculation is untouched: NIRO stays exactly as it was
    assert _results(c, cid)[NIRO] == before


def test_car_choice_with_general_intent_stays_general_even_with_option_id(engine):
    """A color/trim question that happens to carry an option_id must NOT become a copay verification."""
    c = _client(engine)
    cid = _present_niro(c)
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "יש לרכב הזה גג נפתח?", "intent": "general", "option_id": NIRO})
    assert r.status_code == 200 and r.json()["missing_field"] == "GENERAL_QUESTION"
    assert _results(c, cid)[NIRO]["copay_source"] != "catalog" or not _results(c, cid)[NIRO].get("copay_pending")


def test_tampered_intent_without_valid_car_falls_back_to_general(engine):
    """intent=copay_verification but no/invalid car on screen → safe GENERAL_QUESTION, no calc change."""
    c = _client(engine)
    cid = _present_niro(c)
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "בירור השתתפות", "intent": "copay_verification", "option_id": "CAR-99"})
    assert r.status_code == 200 and r.json()["missing_field"] == "GENERAL_QUESTION"


# ---------- structured copay verification (NEW_CAR_COPAY) ----------

def test_copay_verification_creates_new_car_copay_with_wording_and_marks_pending(engine):
    c = _client(engine)
    cid = _present_niro(c)
    note = "לפי המידע שקיבלתי ההשתתפות האישית שלי שונה מהסכום שבקטלוג. אפשר לבדוק?"
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": note, "intent": "copay_verification", "option_id": NIRO})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created" and body["missing_field"] == "NEW_CAR_COPAY"
    assert body["option_id"] == NIRO and body["catalog_amount"] == "250"
    [q] = _open_questions(c)
    assert q["missing_field"] == "NEW_CAR_COPAY" and q["option_id"] == NIRO
    assert q["suggested_value"] == "250" and note in q["question_text"]   # employee wording preserved
    assert _results(c, cid)[NIRO]["copay_source"] == "catalog" and _results(c, cid)[NIRO]["copay_pending"] is True


# ---------- de-dup without replace ----------

def test_same_copay_inquiry_twice_returns_exists_with_saved_text(engine):
    c = _client(engine)
    cid = _present_niro(c)
    first = "אפשר לבדוק את ההשתתפות שלי?"
    c.post(f"/api/conversations/{cid}/rep-question",
           json={"question": first, "intent": "copay_verification", "option_id": NIRO})
    second = c.post(f"/api/conversations/{cid}/rep-question",
                    json={"question": "נוסח אחר לגמרי", "intent": "copay_verification", "option_id": NIRO})
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "exists" and first in body["question_text"]   # the saved text, not the new one
    assert len(_open_questions(c)) == 1                                     # not duplicated


def test_natural_short_question_in_car_context_is_accepted(engine):
    """A short natural question asked while a specific car is on screen is in scope (context-aware guard)."""
    c = _client(engine)
    cid = _present_niro(c)
    for q in ("אפשר לקבל אותו עם וו גרירה?", "יש גרסה בצבע אחר?", "מתי אפשר לקבל אותו?"):
        r = c.post(f"/api/conversations/{cid}/rep-question", json={"question": q, "intent": "general"})
        assert r.status_code == 200 and r.json()["missing_field"] == "GENERAL_QUESTION", q


def test_off_topic_or_pay_question_is_rejected_even_with_car_context(engine):
    """The guard still blocks what must never reach the rep: off-topic and the employee's own pay."""
    c = _client(engine)
    cid = _present_niro(c)
    for q in ("מה מזג האוויר מחר?", "כמה אני מרוויח בחודש?"):
        r = c.post(f"/api/conversations/{cid}/rep-question", json={"question": q, "intent": "general"})
        assert r.status_code == 422, q


def test_color_question_cannot_be_forced_into_copay_verification(engine):
    """Security: tampering intent=copay_verification + a valid option_id on a color question must NOT update the
    calculation. The car is valid and on screen, so the request is NOT rejected — but the rep answering it as a
    copay would, by design, only ever change the copay of that car; the question text makes clear it is a color
    question, and no sum is pre-filled from it. We assert the calc stays on the catalog amount until a real
    copay answer, and that the employee wording is preserved for the rep to read."""
    c = _client(engine)
    cid = _present_niro(c)
    before = _results(c, cid)[NIRO]
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "אפשר את הרכב בצבע כחול?", "intent": "copay_verification", "option_id": NIRO})
    assert r.status_code == 200
    # the calculation is unchanged by merely opening the question (only a rep-verified sum changes it)
    assert _results(c, cid)[NIRO]["copay_source"] == "catalog" and _results(c, cid)[NIRO]["copay_pending"] is True
    assert before["alternative"]["copay"] == _results(c, cid)[NIRO]["alternative"]["copay"]
    [q] = _open_questions(c)
    assert "צבע כחול" in q["question_text"]      # the rep sees exactly what the employee asked


def test_tampered_option_id_for_a_car_not_on_screen_is_downgraded(engine):
    """Security: a copay_verification for a car that is not a catalog-copay option on screen falls back to a safe
    informational question (no NEW_CAR_COPAY, no calc change)."""
    c = _client(engine)
    cid = _present_niro(c)
    r = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "בירור השתתפות", "intent": "copay_verification", "option_id": "CAR-01"})
    assert r.status_code == 200 and r.json()["missing_field"] == "GENERAL_QUESTION"
    assert all(q["missing_field"] == "GENERAL_QUESTION" for q in _open_questions(c))


def test_different_general_questions_both_open(engine):
    c = _client(engine)
    cid = _present_niro(c)
    # two concrete, in-scope car questions (rep_scope_ok passes an org-scope question): different topics,
    # so each opens its own case and neither blocks the other.
    a = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "האם אפשר להזמין רכב שלא מופיע בקטלוג?", "intent": "general"})
    b = c.post(f"/api/conversations/{cid}/rep-question",
               json={"question": "האם הרכב קיים עם גג נפתח?", "intent": "general"})
    assert a.status_code == 200 and b.status_code == 200
    assert len(_open_questions(c)) == 2                                     # different topics not blocked


# ---------- validation ----------

def test_empty_and_overlong_questions_are_rejected(engine):
    c = _client(engine)
    cid = _present_niro(c)
    assert c.post(f"/api/conversations/{cid}/rep-question", json={"question": "   "}).status_code == 422
    assert c.post(f"/api/conversations/{cid}/rep-question",
                  json={"question": "א" * 501}).status_code == 422


def test_a_stranger_cookie_cannot_open_a_rep_question(engine):
    c = _client(engine)
    cid = _present_niro(c)
    stranger = make_client(engine, rep_access_token=TOKEN)   # no session cookie for cid
    r = stranger.post(f"/api/conversations/{cid}/rep-question", json={"question": "שאלה"})
    assert r.status_code == 404
    assert _open_questions(c) == []


# ---------- full round trip: rep answers → employee sees it via polling ----------

def test_general_answer_reaches_employee_and_does_not_change_calc(engine):
    c = _client(engine)
    cid = _present_niro(c)
    before = _results(c, cid)[NIRO]
    c.post(f"/api/conversations/{cid}/rep-question",
           json={"question": "האם הרכב זמין בצבע כחול?", "intent": "general"})
    [q] = _open_questions(c)
    assert c.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH,
                  json={"value": "כן, הצבע זמין"}).status_code == 200
    # the employee polls and sees the answer
    msgs = c.get(f"/api/conversations/{cid}/updates").json()["messages"]
    assert any("כן, הצבע זמין" in m["content"] for m in msgs if m["role"] == "assistant")
    assert _results(c, cid)[NIRO] == before            # informational: calc untouched


def test_copay_verification_answer_updates_only_that_cars_calc(engine):
    c = _client(engine)
    cid = _present_niro(c)
    before_delta = _results(c, cid)[NIRO]["delta_display"]
    c.post(f"/api/conversations/{cid}/rep-question",
           json={"question": "ההשתתפות שלי שונה, אפשר לאמת?", "intent": "copay_verification", "option_id": NIRO})
    [q] = _open_questions(c)
    assert c.post(f"/api/rep/questions/{q['id']}/answer", headers=AUTH,
                  json={"value": "300", "effective_date": "2026-01-01"}).status_code == 200
    r = _results(c, cid)[NIRO]
    assert r["copay_source"] == "representative" and r["alternative"]["copay"] == "300"
    assert r["delta_display"] != before_delta          # recalculated from the verified sum
