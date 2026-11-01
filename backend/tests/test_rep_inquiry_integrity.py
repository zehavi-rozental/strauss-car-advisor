"""One inquiry, one car (stage 1 of the 29/10 plan, R1-R4 / U5).

The car of a copay inquiry is its option_id, never the free text: a card opened for NIRO that still holds a question
about BYD DOLPHIN (the field kept the previous car's text) is refused, so the representative's amount can never land
on a car they were not asked about. An answer for one car changes that car's result only. The employee's chat shows
the employee's own words and the car; the representative keeps the full structured text.
"""
from __future__ import annotations

from .conftest import make_client

TOKEN = "rep-int"
AUTH = {"X-Rep-Token": TOKEN}
PROFILE = ["מנהלת מוצר, שכר בסיס 18500",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
           "יש לי קורולה קרוס, משלמת 300"]
EMAIL = "employee@example.com"


def _code(snapshot, name):
    return next(c.catalog_code for c in snapshot.catalog if name in c.model_name)


def _compare(c, choices):
    cid = c.post("/api/conversations").json()["conversation_id"]
    for m in PROFILE:
        c.post(f"/api/conversations/{cid}/messages", json={"text": m})
    c.post(f"/api/conversations/{cid}/messages", json={"choices": choices, "field": "options"})
    return cid


def _view(c, cid):
    return c.get(f"/api/conversations/{cid}").json()


def _results(c, cid):
    return {r["option_id"]: r["result"] for r in _view(c, cid)["results"]}


def _ask(c, cid, question, option_id=None, intent="copay_verification"):
    return c.post(f"/api/conversations/{cid}/rep-question",
                  json={"question": question, "intent": intent, "email": EMAIL,
                        **({"option_id": option_id} if option_id else {})})


def _rep_questions(c):
    return c.get("/api/rep/questions", headers=AUTH).json()


def _answer(c, qid, value, **extra):
    return c.post(f"/api/rep/questions/{qid}/answer", headers=AUTH, json={"value": value, **extra})


def test_a_niro_inquiry_holding_a_dolphin_question_is_refused_and_nothing_changes(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    cid = _compare(c, [niro, dolphin])
    before = _results(c, cid)
    messages = len(_view(c, cid)["messages"])

    r = _ask(c, cid, "האם ההשתתפות ב-BYD DOLPHIN (2025) היא הסכום שיחול עליי?", niro)

    assert r.status_code == 422
    assert "DOLPHIN" in r.json()["detail"] and "NIRO" in r.json()["detail"]
    assert _rep_questions(c) == []                         # nothing reached the representative
    assert _results(c, cid) == before                      # neither car changed (not even "pending")
    assert len(_view(c, cid)["messages"]) == messages      # nothing said as sent


def test_the_answer_for_one_car_changes_only_that_car(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    cid = _compare(c, [niro, dolphin])
    before = _results(c, cid)

    assert _ask(c, cid, "האם ההשתתפות ב-NIRO היא הסכום שיחול עליי?", niro).json()["status"] == "created"
    assert _ask(c, cid, "אפשר לאשר לי את ההשתתפות ברכב הזה?", dolphin).json()["status"] == "created"
    by_car = {q["option_id"]: q for q in _rep_questions(c)}
    assert set(by_car) == {niro, dolphin}
    assert by_car[niro]["context"]["car_label"].startswith("NIRO")
    assert "DOLPHIN" in by_car[dolphin]["context"]["car_label"]

    # the representative answers DOLPHIN: NIRO stays exactly as it was (still pending its own answer)
    pending_niro = _results(c, cid)[niro]
    assert _answer(c, by_car[dolphin]["id"], "320").status_code == 200
    after = _results(c, cid)
    assert after[niro] == pending_niro
    assert after[dolphin]["copay_source"] == "representative"
    assert after[dolphin]["alternative"]["copay"] == "320"
    assert after[dolphin]["delta_display"] != before[dolphin]["delta_display"]

    # then NIRO, without a note: only NIRO changes, DOLPHIN keeps the representative's 320
    assert _answer(c, by_car[niro]["id"], "270").status_code == 200
    final = _results(c, cid)
    assert final[dolphin] == after[dolphin]
    assert final[niro]["copay_source"] == "representative" and final[niro]["alternative"]["copay"] == "270"
    last = _view(c, cid)["messages"][-1]["content"]
    assert "NIRO" in last and "DOLPHIN" not in last


def test_the_chat_shows_the_employee_wording_and_the_rep_keeps_the_structured_text(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    question = "סוכם איתי על השתתפות אחרת ב-NIRO, אפשר לבדוק?"

    body = _ask(c, cid, question, niro).json()

    assert body["question_text"] == question
    [q] = _rep_questions(c)
    assert q["question_text"].startswith("מה ההשתתפות החודשית של העובד ב-NIRO")   # the rep's full text
    assert q["question_text"].endswith(question)
    view = _view(c, cid)
    sent = [m for m in view["messages"] if m["source"] == "rep_inquiry"]
    assert len(sent) == 1 and sent[0]["rep_question_id"] == q["id"]
    assert sent[0]["content"].startswith(f"פנייה לנציג הרכב בנושא {q['context']['car_label']}:\n{question}\n\n")
    assert EMAIL not in sent[0]["content"]                 # the address is never written into the chat
    assert "של העובד" not in sent[0]["content"] and "לפי הקטלוג" not in sent[0]["content"]
    [mine] = view["rep_questions"]
    assert mine["employee_text"] == question and mine["option_id"] == niro


def test_a_general_question_carries_the_car_it_names_not_todays_car(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])

    assert _ask(c, cid, "באיזה צבעים יש את NIRO?", intent="general").status_code == 200
    assert _ask(c, cid, "אפשר לקבל אותו עם וו גרירה?", intent="general").status_code == 200

    by_text = {q["question_text"]: q for q in _rep_questions(c)}
    assert by_text["באיזה צבעים יש את NIRO?"]["context"]["car_label"].startswith("NIRO")
    assert "COROLLA" in by_text["אפשר לקבל אותו עם וו גרירה?"]["context"]["car_label"]   # no car named: today's
    assert all(r["copay_source"] != "representative" for r in _results(c, cid).values())


def test_an_inquiry_naming_its_own_car_in_another_form_is_accepted(engine, snapshot):
    """The check refuses another car only: the same car, or a name inside its own ("COROLLA" for COROLLA CROSS),
    passes."""
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    r = _ask(c, cid, "ההשתתפות ב-NIRO (2025) שונה אצלי, אפשר לבדוק?", niro)
    assert r.status_code == 200 and r.json()["status"] == "created"


# ---------- the representative's answer (stage 2, A1-A3) ----------

ANSWER_KEYS = {"id", "missing_field", "mode", "option_id", "question_text", "answer_type", "suggested_value",
               "context", "status", "created_at", "answered_at", "answer_value", "effective_date", "answer_note",
               "answered_by"}


def _rep_messages(c, cid):
    return [m["content"] for m in _view(c, cid)["messages"] if m["source"] == "rep"]


def test_a_copay_is_approved_without_a_note_and_survives_a_refresh(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    cid = _compare(c, [niro, dolphin])
    _ask(c, cid, "אפשר לאשר לי את ההשתתפות ב-NIRO?", niro)
    [q] = _rep_questions(c)
    before_dolphin = _results(c, cid)[dolphin]

    r = _answer(c, q["id"], "250")                       # the catalog amount, in one click, no note

    assert r.status_code == 200 and set(r.json()) == ANSWER_KEYS     # the answer contract is unchanged
    assert r.json()["answer_note"] is None
    for _ in range(2):                                   # a refresh reads the same, once
        [msg] = _rep_messages(c, cid)
        assert "250" in msg and "הערת הנציג" not in msg
        results = _results(c, cid)
        assert results[niro]["copay_source"] == "representative" and results[dolphin] == before_dolphin


def test_a_note_is_shown_once_with_its_lines_and_punctuation(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    _ask(c, cid, "אפשר לאשר לי את ההשתתפות ב-NIRO?", niro)
    [q] = _rep_questions(c)
    note = "סוכם מול המחלקה.\nההשתתפות תעודכן בתלוש הבא - בלי צורך בפעולה נוספת!"

    assert _answer(c, q["id"], "270", note=note).status_code == 200

    [msg] = _rep_messages(c, cid)
    assert msg.count("הערת הנציג") == 1
    assert f"הערת הנציג: {note}" in msg                  # line break, hyphen and punctuation kept
    assert note not in str(_results(c, cid))             # never repeated on the car's card
    assert c.get("/api/rep/questions?status=ANSWERED", headers=AUTH).json()[0]["answer_note"] == note


def test_a_general_answer_in_several_lines_changes_no_calculation(engine, snapshot):
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    before = _results(c, cid)
    _ask(c, cid, "באיזה צבעים יש את NIRO?", intent="general")
    [q] = _rep_questions(c)
    answer = "לבן, שחור ואפור.\nצבע אדום – בהזמנה מיוחדת בלבד."

    assert _answer(c, q["id"], answer).status_code == 200

    for _ in range(2):
        [msg] = _rep_messages(c, cid)
        assert msg.count(answer) == 1 and "הערת הנציג" not in msg
        assert _results(c, cid) == before                # informational only
        assert answer not in str(_results(c, cid))


def test_a_legacy_effective_date_is_still_accepted_by_the_api(engine, snapshot):
    """The field left the rep screen, not the API: an older client that sends it still gets 200."""
    c = make_client(engine, rep_access_token=TOKEN)
    niro = _code(snapshot, "NIRO")
    cid = _compare(c, [niro])
    _ask(c, cid, "אפשר לאשר לי את ההשתתפות ב-NIRO?", niro)
    [q] = _rep_questions(c)
    r = _answer(c, q["id"], "250", effective_date="2026-11-01")
    assert r.status_code == 200 and r.json()["effective_date"] == "2026-11-01"


def test_an_inquiry_comparing_its_car_with_another_is_refused_and_says_where_to_ask(engine, snapshot):
    """The stage-1 guard on a copay inquiry is deliberate: a copay answer is one amount for one car, so a text that
    names a second car could be answered for the wrong one. The message says how to ask about both."""
    c = make_client(engine, rep_access_token=TOKEN)
    niro, dolphin = _code(snapshot, "NIRO"), _code(snapshot, "DOLPHIN")
    cid = _compare(c, [niro, dolphin])
    r = _ask(c, cid, "ההשתתפות ב-NIRO גבוהה יותר מאשר ב-DOLPHIN?", niro)
    assert r.status_code == 422 and "פתחו פנייה מהכרטיס" in r.json()["detail"]
    # the same comparison as a general question is not blocked: no amount is answered there
    g = _ask(c, cid, "ההשתתפות ב-NIRO גבוהה יותר מאשר ב-DOLPHIN?", intent="general")
    assert g.status_code == 200 and g.json()["missing_field"] == "GENERAL_QUESTION"
