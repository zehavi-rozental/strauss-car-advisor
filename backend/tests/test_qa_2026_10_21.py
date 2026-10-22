"""QA round 21/10: the two cloud conversations (qa/2026-10-21/שיחה.txt, שיחה2.txt), message by message, plus the
syncing between chat, form, payslip review, profile, results and the representative.

Template mode (no LLM) unless a FakeLLM scripts what the live model evidently did. Each assertion names the
failure seen in the cloud. FakeLLM tests check our code's guards around a model, not the live model itself.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage, results_stale
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client

PROFILE = ("מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
Q2_MIXED = "בונוס-1000 פנסיה-5 קרן השתלמות-6\nאין נקודות זיכוי וילדים מתחת גיל 23"
TOKEN = "rep-secret"
AUTH = {"X-Rep-Token": TOKEN}


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def talk(svc, *texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def state(svc, turn, token):
    return svc.view(turn.conversation_id, token).state


def asking_lines(reply: str) -> list[str]:
    return [ln for ln in reply.splitlines() if "?" in ln]


# ---------- T1: "יש לי רכב חברה" is a fact, not a filler ----------

def test_a_company_car_said_with_the_group_is_kept(sessions):
    svc = service(sessions)
    turn, token = talk(svc, "יש לי רכב חברה ואני בקבוצה ב")
    s = state(svc, turn, token)
    assert s.profile.group == "ב"
    assert s.current.has_car is True                    # cloud: lost; later "כתבתי לך שיש לי רכב"
    assert "רכב צמוד" in turn.reply                      # acknowledged, not dropped silently


# ---------- T1: one Q2 message with valid, invalid, held and unclear parts ----------

def _to_q2(svc):
    return talk(svc, "יש לי רכב חברה ואני בקבוצה ב", "16000")


def test_a_mixed_q2_message_saves_the_valid_values_and_says_what_was_refused(sessions):
    svc = service(sessions)
    turn, token = _to_q2(svc)
    turn = svc.send(turn.conversation_id, token, Q2_MIXED)
    s = state(svc, turn, token)
    p = s.profile
    assert p.bonus == Decimal(1000) and p.pension_percent == Decimal(5)       # never lost with the rest
    assert p.study_fund_percent is None                                       # 6% is outside 0–2.5
    assert p.children is None                                                 # "גיל 23" is not a count
    assert p.credit_points is None                                            # 0 is held, not saved
    assert s.pending_confirmation is not None and s.pending_confirmation.field == "credit_points"
    assert "בונוס 1,000 ₪" in turn.reply and "פנסיה 5%" in turn.reply           # what was saved
    assert "קרן השתלמות" in turn.reply and "6%" in turn.reply                  # what was refused, and why
    # exactly one question, and the call to action answers that same question
    asks = asking_lines(turn.reply)
    assert len(asks) == 1 and "נקודות זיכוי" in asks[0], turn.reply
    assert turn.reply.rstrip().endswith("או כתבו את הערך הנכון.") and "לאשר" in turn.reply


def test_fixing_the_held_value_asks_only_that_field_and_binds_the_answer(sessions):
    svc = service(sessions)
    turn, token = _to_q2(svc)
    svc.send(turn.conversation_id, token, Q2_MIXED)
    turn = svc.send(turn.conversation_id, token, "לתקן")
    asks = asking_lines(turn.reply)
    assert len(asks) == 1 and "נקודות זיכוי" in asks[0], turn.reply          # cloud: + the car question
    assert state(svc, turn, token).asked_field == "credit_points"
    turn = svc.send(turn.conversation_id, token, "3\nיש קורולה ומשלמת עליו אני לא זוכרת כמה")
    s = state(svc, turn, token)
    assert s.profile.credit_points == Decimal(3)
    assert s.current.copay_unknown                                            # "לא זוכרת כמה"
    assert [c.label for c in s.current.choices] == ["COROLLA (2026)", "COROLLA CROSS (2025)"]


def test_the_model_cannot_read_an_age_as_a_child_count_or_a_far_zero(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = _to_q2(svc)
    fake.turn(call("save_profile", bonus=1000, pension_percent=5, study_fund_percent=6, credit_points=0,
                   children_under_23=23))
    turn = svc.send(turn.conversation_id, token, Q2_MIXED)
    p = state(svc, turn, token).profile
    assert p.children is None and "23" not in turn.reply.replace("גיל 23", "")
    fake.turn(call("save_profile", children_under_23=0))
    turn = svc.send(turn.conversation_id, token, "אין נקודות זיכוי וילדים מתחת גיל 23")
    assert state(svc, turn, token).profile.children is None    # "אין" belongs to the credit points


# ---------- T1: "התכוונתי לרכב אחר" keeps the fact that there is a car ----------

def _with_car(svc):
    turn, token = talk(svc, "יש לי רכב חברה ואני בקבוצה ב", "16000",
                       "אין עמלות, אין בונוס, פנסיה 5%, קרן השתלמות 2.5%, 3 נקודות זיכוי, אין ילדים",
                       "יש לי קורולה ומשלמת עליו אני לא זוכרת כמה")
    return svc.send(turn.conversation_id, token, "2"), token          # COROLLA CROSS (2025)


def test_another_car_meant_resets_only_the_model(sessions):
    svc = service(sessions)
    turn, token = _with_car(svc)
    assert state(svc, turn, token).current.label == "COROLLA CROSS (2025)"
    turn = svc.send(turn.conversation_id, token, "התכוונתי לרכב אחר")
    s = state(svc, turn, token)
    assert s.current.source is None and s.current.has_car is True and s.current.copay_unknown
    assert "אין רכב" not in turn.reply                                  # cloud: asked as if no car was said
    assert "דגם" in turn.reply
    turn = svc.send(turn.conversation_id, token, "כתבתי לך שיש לי רכב למה אתה שואל שוב?")
    assert "נציג" not in turn.reply and "סליחה" not in turn.reply      # answered from the state
    assert "רכב צמוד" in turn.reply
    turn = svc.send(turn.conversation_id, token, "קורולה")
    s = state(svc, turn, token)
    assert s.current.label == "COROLLA (2026)"                          # the other Corolla, said explicitly
    assert "זיהיתי: COROLLA (2026)" in turn.reply


# ---------- T2: a car named after "no car" ----------

def test_a_car_named_at_the_maintenance_question_does_not_undo_no_car(sessions):
    svc = service(sessions)
    turn, token = talk(svc, "אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום",
                       "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2 נקודות זיכוי, אין ילדים")
    assert state(svc, turn, token).current.source == "none"
    turn = svc.send(turn.conversation_id, token, "קורולה")
    assert state(svc, turn, token).current.source == "none"            # never silently replaced


def test_a_salary_correction_while_choosing_is_saved_not_audited(sessions):
    svc = service(sessions)
    turn, token = talk(svc, "אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום",
                       "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2 נקודות זיכוי, אין ילדים", "אין")
    assert turn.stage == Stage.CHOOSE_OPTIONS
    turn = svc.send(turn.conversation_id, token, "טעיתי, השכר שלי 16,000")
    assert state(svc, turn, token).profile.base == Decimal(16000)
    assert "16,000" in turn.reply and "בדקתי" not in turn.reply


@pytest.mark.parametrize("text, calls", [
    ("לא ידוע לי", [call("save_profile", unknown_fields=["commissions", "bonus", "pension_percent",
                                                         "study_fund_percent", "credit_points", "children"])]),
    ("הכל 5", [call("save_profile", commissions=5, pension_percent=5, study_fund_percent=5, credit_points=5)]),
])
def test_a_blanket_answer_is_never_spread_by_the_model(sessions, text, calls):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = talk(svc, "אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום")
    fake.turn(*calls)
    turn = svc.send(turn.conversation_id, token, text)
    p = state(svc, turn, token).profile
    assert len(p.unknown_fields) <= 1
    assert sum(getattr(p, f) is not None for f in ("commissions", "pension_percent", "study_fund_percent",
                                                   "credit_points")) == 0


# ---------- form and payslip review → profile → result ----------

@pytest.fixture
def client(engine):
    return make_client(engine, rep_access_token=TOKEN)


def _chat(client, *texts):
    cid = client.post("/api/conversations").json()["conversation_id"]
    body = None
    for t in texts:
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": t}).json()
    return cid, body


def _fields(client, cid, fields, **extra):
    return client.post(f"/api/conversations/{cid}/messages", json={"fields": fields, **extra})


def test_a_rejected_form_names_every_bad_field_and_saves_nothing(client):
    cid, _ = _chat(client, "אני בקבוצה ג, שכר בסיס 15,000")
    r = _fields(client, cid, {"bonus": 1000, "study_fund_percent": 6, "pension_percent": 30})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert set(errors) == {"study_fund_percent", "pension_percent"}
    assert client.get(f"/api/conversations/{cid}").json()["profile"]["bonus"] is None


def test_a_form_never_drops_a_value_held_from_chat(client):
    cid, body = _chat(client, "אני בקבוצה ג, שכר בסיס 15,000", "פנסיה 0")
    assert "לאשר" in body["reply"]                                    # pension 0 is held for confirmation
    body = _fields(client, cid, {"bonus": 1000}).json()
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["profile"]["bonus"] == "1000" and view["profile"]["pension_percent"] is None
    assert "לאשר" in body["reply"] and "פנסיה" in body["reply"]          # still asked, not dropped


def test_a_form_reports_only_what_it_saved(client):
    cid, _ = _chat(client, "אני בקבוצה ג, שכר בסיס 15,000")
    body = _fields(client, cid, {"bonus": 1000, "pension_percent": 0, "credit_points": 0}).json()
    view = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert view["credit_points"] is None
    assert "0 נקודות זיכוי" not in body["reply"].split("\n")[0]        # cloud: "רשמתי" for a held value


def test_a_payslip_value_replaces_the_chat_value_with_an_explicit_update(client):
    cid, _ = _chat(client, "אני מנהלת מוצר, שכר בסיס 15,000, אין עמלות ואין בונוס, פנסיה 6%, "
                           "קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
                   "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    before = client.get(f"/api/conversations/{cid}").json()["results"][0]["result"]["today"]["net_display"]
    body = _fields(client, cid, {"base_salary": 18000, "pension_percent": 6}, source="payslip").json()
    assert "עדכנתי מהתלוש: שכר בסיס 18,000 ₪ במקום 15,000 ₪." in body["reply"]
    assert "פנסיה" not in body["reply"].split("\n")[0]                 # unchanged values are not "updated"
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["profile"]["base"] == "18000"
    assert view["results"][0]["result"]["today"]["net_display"] != before                # recalculated on the new base
    user = [m for m in view["messages"] if m["role"] == "user"][-1]["content"]
    assert user.startswith("מהתלוש (אושר):")


# ---------- results that are out of date ----------

def test_a_result_that_cannot_be_recalculated_is_marked_stale(client):
    cid, body = _chat(client, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    assert body["results"] and body["results_stale"] is False
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "זה לא הרכב שלי"}).json()
    updates = client.get(f"/api/conversations/{cid}/updates").json()
    assert updates["results"] and updates["results_stale"] is True
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["results_stale"] is True


def test_the_model_never_sees_a_stale_result_as_current(sessions):
    from app.modules.conversation.turn import state_block
    from app.modules.rules.public import load_snapshot
    svc = service(sessions)
    turn, token = talk(svc, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    turn = svc.send(turn.conversation_id, token, "זה לא הרכב שלי")
    block = state_block(state(svc, turn, token), load_snapshot(CONFIG))
    assert "net today" not in block and "out of date" in block


def test_an_invalidated_result_is_stale_until_recalculated_and_never_quoted(sessions):
    from app.modules.conversation.public import results_stale
    from app.modules.conversation.turn import state_block
    from app.modules.rules.public import load_snapshot
    svc = service(sessions)
    turn, token = talk(svc, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    s = state(svc, turn, token)
    assert s.results and not results_stale(s)
    s.calculated_for = []                     # what every save does (tools._invalidate_results)
    assert results_stale(s)
    block = state_block(s, load_snapshot(CONFIG))
    assert "out of date" in block and "net today" not in block


# ---------- the representative's note ----------

def _rep_flow(client):
    cid, body = _chat(client, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3",
                      "אני רוצה שנציג התחבורה יאשר את ההשתתפות")   # the rep only when asked (22/10)
    qid = client.get("/api/rep/questions", headers=AUTH).json()[0]["id"]
    return cid, qid


def _messages(client, cid):
    return client.get(f"/api/conversations/{cid}/updates").json()["messages"]


def test_the_reps_note_reaches_the_employee(client, engine):
    second = make_client(engine, rep_access_token=TOKEN)          # another employee (another cookie)
    other, other_qid = _rep_flow(second)
    cid, _ = _rep_flow(client)
    qid = next(q["id"] for q in client.get("/api/rep/questions", headers=AUTH).json() if q["id"] != other_qid)
    r = client.post(f"/api/rep/questions/{qid}/answer", headers=AUTH,
                    json={"value": "250", "note": "האישור בתוקף מהחודש הבא"})
    assert r.status_code == 200
    rep = [m for m in _messages(client, cid) if m["source"] == "rep"]
    assert rep and "הערת הנציג: האישור בתוקף מהחודש הבא" in rep[-1]["content"]
    assert not [m for m in _messages(second, other) if m["source"] == "rep"]     # only that conversation


def test_no_note_no_line(client):
    cid, qid = _rep_flow(client)
    client.post(f"/api/rep/questions/{qid}/answer", headers=AUTH, json={"value": "250"})
    rep = [m for m in _messages(client, cid) if m["source"] == "rep"]
    assert rep and "הערת הנציג" not in rep[-1]["content"] and "אין תגובה" not in rep[-1]["content"]


def test_a_note_is_plain_text(client):
    cid, qid = _rep_flow(client)
    client.post(f"/api/rep/questions/{qid}/answer", headers=AUTH,
                json={"value": "250", "note": "**דחוף**\n- <script>alert(1)</script>\x07 [קישור](http://x.y)"})
    content = [m for m in _messages(client, cid) if m["source"] == "rep"][-1]["content"]
    note = content.split("הערת הנציג: ", 1)[1].split("\n")[0]
    assert "**" not in note and "<script>" not in note and "\x07" not in note and "](" not in note


def test_a_rep_answer_on_a_stale_result_reports_the_amount_not_an_old_difference(client):
    cid, qid = _rep_flow(client)                              # ATTO 3 calculated with the catalog copay
    client.post(f"/api/conversations/{cid}/messages", json={"text": "זה לא הרכב שלי"})
    assert client.get(f"/api/conversations/{cid}").json()["results_stale"] is True
    client.post(f"/api/rep/questions/{qid}/answer", headers=AUTH, json={"value": "350"})
    content = [m for m in _messages(client, cid) if m["source"] == "rep"][-1]["content"]
    assert "ההפרש עודכן" not in content and "350" in content     # no before/after from an out-of-date result
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["results_stale"] is True                       # recalculated only once the car is known again


def test_the_model_is_told_there_is_a_company_car(sessions):
    from app.modules.conversation.turn import state_block
    from app.modules.rules.public import load_snapshot
    svc = service(sessions)
    turn, token = talk(svc, "יש לי רכב חברה ואני בקבוצה ב")
    block = state_block(state(svc, turn, token), load_snapshot(CONFIG))
    assert "HAS a company car" in block


# ---------- the two scenarios left open in the first report ----------

Q2_OPEN = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")


def test_all_5_in_template_mode_saves_nothing_and_asks_one_field_at_a_time(sessions):
    svc = service(sessions)
    turn, token = talk(svc, "אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום")
    turn = svc.send(turn.conversation_id, token, "הכל 5")
    s = state(svc, turn, token)
    assert all(getattr(s.profile, f) is None for f in Q2_OPEN)            # nothing confirmed by a blanket value
    assert s.pending_confirmation is None and s.pace == "one_at_a_time"
    assert len(asking_lines(turn.reply)) == 1 and s.asked_field is not None   # one field, named
    turn = svc.send(turn.conversation_id, token, "5")                      # answers only that field
    s = state(svc, turn, token)
    assert sum(getattr(s.profile, f) is not None for f in Q2_OPEN) + (s.pending_confirmation is not None) == 1


def test_another_car_after_a_result_waits_for_the_model_and_only_then_recalculates(sessions):
    svc = service(sessions)
    turn, token = talk(svc, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    before = state(svc, turn, token).results[0].result["delta_display"]
    turn = svc.send(turn.conversation_id, token, "התכוונתי לרכב אחר")
    s = state(svc, turn, token)
    assert s.current.source is None and s.current.has_car and results_stale(s)
    assert not turn.results and turn.stage == Stage.COLLECT_CURRENT_CAR     # no result shown as current
    turn = svc.send(turn.conversation_id, token, "הכי משתלם")              # not ranked from the old result
    s = state(svc, turn, token)
    assert results_stale(s) and s.selected == ["CAR-10"] and not turn.results
    turn = svc.send(turn.conversation_id, token, "קורולה 2026")
    s = state(svc, turn, token)
    assert not results_stale(s) and turn.results and s.current.label == "COROLLA (2026)"
    assert s.results[0].result["delta_display"] != before                  # calculated for the new car


# ---------- every path that changes calculation data: a fresh result, or one marked stale ----------

def _fresh_or_stale(svc, cid, token):
    from app.modules.calculation.public import evaluate
    from app.modules.conversation.tools import available_options, engine_inputs, option_state
    from app.modules.rules.public import load_snapshot
    s = svc.view(cid, token).state
    if results_stale(s):
        return "stale"
    snap = load_snapshot(CONFIG)
    employee, current, occupation = engine_inputs(snap, s)
    options = {o.id: o for o in available_options(snap, s)}
    for item in s.results:
        confirmed = s.confirmed_copays.get(item.option_id)
        fresh = evaluate(employee, current, option_state(options[item.option_id], snap.extensions.copay_policy,
                                                         confirmed.value if confirmed else None), snap.calc, occupation)
        assert str(fresh.delta_display) == str(item.result["delta_display"]), item.label
    return "fresh"


@pytest.mark.parametrize("kind, change", [
    ("text", "טעיתי, השכר שלי 21,000"),                      # salary in chat
    ("text", "בעצם פנסיה 7%"),                                # another salary field in chat
    ("fields", {"bonus": 1500}),                              # the correction form
    ("payslip", {"base_salary": 19000, "credit_points": 3}),  # a reviewed payslip
    ("text", "זה לא הרכב שלי"),                               # the car today reset
    ("text", "התבלבלתי, יש לי קורולה 2026"),                  # the car today corrected
    ("text", "MG4"),                                          # another alternative
])
def test_no_path_shows_an_old_result_as_current(sessions, kind, change):
    svc = service(sessions)
    turn, token = talk(svc, PROFILE, "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש", "אטו 3")
    cid = turn.conversation_id

    def data(st):
        return (st.profile.model_dump(), st.current.label, st.current.source, list(st.selected))

    before = data(state(svc, turn, token))
    if kind == "text":
        svc.send(cid, token, change)
    else:
        svc.send_fields(cid, token, dict(change), "payslip" if kind == "payslip" else None)
    s = svc.view(cid, token).state
    assert data(s) != before, "the change was not applied"
    assert _fresh_or_stale(svc, cid, token) in ("fresh", "stale")   # fresh = equal to the engine now


# ---------- API compatibility and the rep boundary ----------

def test_the_message_contract_stays_backward_compatible(client):
    cid, _ = _chat(client, "אני בקבוצה ג, שכר בסיס 15,000")
    url = f"/api/conversations/{cid}/messages"
    assert client.post(url, json={"fields": {"bonus": 1000}}).status_code == 200           # source is optional
    assert client.post(url, json={"fields": {"bonus": 900}, "source": "payslip"}).status_code == 200
    assert client.post(url, json={"fields": {"bonus": 800}, "source": "other"}).status_code == 422
    assert client.post(url, json={"text": "שלום", "source": "payslip"}).status_code == 422  # fields only
    bad = client.post(url, json={"fields": {"pension_percent": 30}}).json()
    assert isinstance(bad["detail"], str) and bad["message_he"] == bad["detail"]           # old readers: detail
    assert set(bad["errors"]) == {"pension_percent"}
    body = client.post(url, json={"text": "אין עמלות"}).json()
    assert body["results_stale"] is False and set(body) >= {"reply", "stage", "expects", "results"}


def test_the_rep_sees_no_salary_and_no_conversation_even_after_a_note(client):
    cid, qid = _rep_flow(client)
    client.post(f"/api/rep/questions/{qid}/answer", headers=AUTH, json={"value": "250", "note": "בתוקף מיד"})
    q = client.get(f"/api/rep/questions/{qid}", headers=AUTH).json()
    text = str(q)
    assert "conversation_id" not in q and cid not in text
    assert "18500" not in text and "18,500" not in text and "profile" not in q
    assert client.get(f"/api/rep/questions/{qid}").status_code == 401                         # still needs the token


# ---------- the engine never ranks or calculates on a value the employee did not give (rule 4) ----------

def test_best_option_waits_for_an_unknown_salary_field_and_then_ranks_on_the_real_value(sessions):
    svc = service(sessions)
    turn, token = talk(svc, "מנהלת מוצר, שכר בסיס 18,500, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
                            "2.75 נקודות זיכוי, 2 ילדים", "לא יודעת כמה עמלות", "יש לי קורולה קרוס", "300")
    turn = svc.send(turn.conversation_id, token, "הכי משתלם")
    s = state(svc, turn, token)
    assert s.selected == [] and s.ranked_count == 0 and s.rank_requested     # nothing chosen on a guessed 0
    assert "עמלות" in turn.reply                                            # says what is missing
    turn = svc.send(turn.conversation_id, token, "עמלות 3,000")
    s = state(svc, turn, token)
    assert s.profile.commissions == Decimal(3000) and len(s.selected) == 3 and turn.results


@pytest.mark.parametrize("missing", ["commissions", "bonus", "study_fund_percent", "children"])
def test_engine_inputs_refuse_a_missing_value_instead_of_reading_zero(missing):
    from app.modules.conversation.stage import ConversationState, CurrentCar, Profile
    from app.modules.conversation.tools import MissingInputs, engine_inputs
    from app.modules.rules.public import load_snapshot
    values = dict(group="ג", base=Decimal(18500), commissions=Decimal(0), bonus=Decimal(0),
                  pension_percent=Decimal(6), study_fund_percent=Decimal("2.5"), credit_points=Decimal("2.75"),
                  children=2)
    state_ = ConversationState(profile=Profile(**values), current=CurrentCar(source="none", maintenance=Decimal(0)))
    engine_inputs(load_snapshot(CONFIG), state_)                           # complete: accepted
    state_.profile = Profile(**{**values, missing: None})
    with pytest.raises(MissingInputs):
        engine_inputs(load_snapshot(CONFIG), state_)                       # never read as 0
