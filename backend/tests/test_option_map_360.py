"""STRAUSS DRIVE 360° acceptance: the option map lists every eligible option, each calculated by the engine,
with an honest status, a lead only among what was calculated, and never a value that was not given.

Template mode (no LLM) unless a FakeLLM is named; the numbers are compared with the engine itself."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.calculation.public import FAMILY_GROUP
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.option_map import (NET_ONLY, build_option_map, evaluate_option, leaders, map_json,
                                                 map_reply)
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.stage import ConversationState, CurrentCar, MapEntry, OptionMap, Profile
from app.modules.conversation.tools import available_options, engine_inputs
from app.modules.rules.public import RulesStore, load_snapshot

from .conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
ASK = "השווה את כל האפשרויות שלי"
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"
TOKEN = "rep-secret"


def profile(occupation: str, children: int = 2, base: str = "18,500") -> str:
    kids = "אין ילדים" if children == 0 else f"{children} ילדים"
    return (f"אני {occupation}, שכר בסיס {base}, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
            f"2.75 נקודות זיכוי, {kids}")


def service(sessions, fake=None):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]) if fake else LLMChain())


def talk(svc, *texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def mapped(svc, turn, token):
    s = svc.view(turn.conversation_id, token).state
    return s, map_json(s)


def groups(m):
    return {e["group"] for e in m["entries"] if e["group"]}


# ---------- 1–5: eligibility comes from the rules, never from the model ----------

def test_group_b_sees_no_car_above_its_group(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מפתחת תוכנה"), CAR, ASK)
    _, m = mapped(svc, turn, token)
    assert groups(m) <= {"א", "ב"}
    assert {x["group"] for x in m["excluded"]} >= {"ג", "ד"}
    assert all("מעל קבוצת הזכאות" in x["reason"] for x in m["excluded"] if x["group"] in ("ג", "ד"))


def test_group_d_also_gets_every_lower_group(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהל מחלקה", base="32,000"), CAR, ASK)
    _, m = mapped(svc, turn, token)
    assert groups(m) == {"א", "ב", "ג", "ד"}


def test_the_family_track_needs_its_number_of_children(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר", children=4), CAR, ASK)
    _, with_kids = mapped(svc, turn, token)
    assert FAMILY_GROUP in groups(with_kids)
    turn, token = talk(svc, profile("מנהלת מוצר", children=2), CAR, ASK)
    _, without = mapped(svc, turn, token)
    assert FAMILY_GROUP not in groups(without)
    assert [x for x in without["excluded"] if x["group"] == FAMILY_GROUP
            and "מסלול משפחתי" in x["reason"]]


def test_opting_out_appears_only_when_the_occupation_allows_it(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    _, allowed = mapped(svc, turn, token)
    assert "OPT_OUT" in [e["option_id"] for e in allowed["entries"]]
    turn, token = talk(svc, profile("מנהל אזור מכירות"), CAR, ASK)
    _, mandatory = mapped(svc, turn, token)
    assert "OPT_OUT" not in [e["option_id"] for e in mandatory["entries"]]
    assert any("לא ניתן לוותר" in x["reason"] for x in mandatory["excluded"])


# ---------- 6–8: one request, every option, the engine's numbers ----------

def test_the_request_needs_no_chosen_car_and_lists_every_eligible_option(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    s, m = mapped(svc, turn, token)
    assert s.selected == [] and s.ranked_count == 0                      # nothing chosen for the employee
    assert len(m["entries"]) == len(available_options(SNAP, s)) == 13 > 3
    assert "מיפיתי את כל 13 החלופות" in turn.reply and NET_ONLY in turn.reply
    assert "הכי משתלם" not in turn.reply.split("\n\n")[0]


def test_every_net_and_difference_is_the_engines(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    s, m = mapped(svc, turn, token)
    employee, current, occupation = engine_inputs(SNAP, s)
    options = {o.id: o for o in available_options(SNAP, s)}
    for e in m["entries"]:
        _, fresh = evaluate_option(SNAP, s, options[e["option_id"]], employee, current, occupation)
        assert str(fresh.delta_display) == e["result"]["delta_display"], e["label"]
        assert str(fresh.alternative.net_display) == e["result"]["alternative"]["net_display"], e["label"]
    deltas = [Decimal(e["result"]["delta_display"]) for e in m["entries"] if e["status"] in ("full", "catalog_copay")]
    assert deltas == sorted(deltas, reverse=True)                          # sorted by net, high to low


def test_the_request_asked_first_waits_for_the_data_and_asks_only_what_is_missing(sessions):
    svc = service(sessions)
    turn, token = talk(svc, ASK)
    assert "כדי למפות את כל האפשרויות, חסרים לי עוד" in turn.reply and "הרכבים להשוואה" not in turn.reply
    turn = svc.send(turn.conversation_id, token, profile("מנהלת מוצר"))
    assert mapped(svc, turn, token)[1] is None                            # no car yet: nothing calculated
    turn = svc.send(turn.conversation_id, token, CAR)
    assert "מיפיתי את כל 13 החלופות" in turn.reply                         # shown as soon as it is ready


# ---------- 9, 14, 19, 20: only what was calculated is ranked; ties; nothing to rank ----------

def test_an_option_missing_a_value_is_listed_but_never_ranked(sessions):
    svc = service(sessions)        # a group without an occupation: opting out needs the representative's answer
    turn, token = talk(svc, "אני בקבוצה ג, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
                            "2.75 נקודות זיכוי, 2 ילדים", CAR, ASK)
    _, m = mapped(svc, turn, token)
    opt_out = next(e for e in m["entries"] if e["option_id"] == "OPT_OUT")
    assert opt_out["status"] == "incomplete" and "OPT_OUT" not in m["leaders"]
    assert m["counts"]["waiting"] == 1 and m["entries"][-1]["option_id"] == "OPT_OUT"   # listed after the ranked
    assert "1 חלופות ממתינות למידע ולא נכללו בדירוג" in turn.reply


def _entry(code: str, delta: str, status: str = "catalog_copay") -> MapEntry:
    result = {"status": "OK" if status != "incomplete" else "INCOMPLETE",
              "delta_display": delta if status != "incomplete" else None}
    return MapEntry(option_id=code, label=code, status=status, result=result)


def test_a_tie_names_no_single_winner():
    m = OptionMap(entries=[_entry("A", "500"), _entry("B", "500"), _entry("C", "100")])
    assert [e.option_id for e in leaders(m)] == ["A", "B"]
    reply = map_reply(ConversationState(option_map=m))
    assert "שוויון בנטו המשוער בין A ו-B" in reply and "הנטו החודשי הגבוה ביותר" not in reply


def test_nothing_calculated_means_no_ranking():
    m = OptionMap(entries=[_entry("A", "0", "incomplete"), _entry("B", "0", "incomplete")])
    assert leaders(m) == []
    reply = map_reply(ConversationState(option_map=m))
    assert "אין עדיין חלופה שחושבה במלואה, ולכן אין דירוג" in reply and NET_ONLY not in reply


def test_an_out_of_date_map_has_no_leader():
    m = OptionMap(entries=[_entry("A", "500"), _entry("B", "100")], fresh=False)
    assert leaders(m) == [] and map_json(ConversationState(option_map=m))["stale"] is True


# ---------- 10–12: changes, the representative, a reload ----------

def test_a_salary_change_rebuilds_the_map_on_the_new_value(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    _, before = mapped(svc, turn, token)
    turn = svc.send(turn.conversation_id, token, "טעיתי, השכר שלי 25,000")
    s, after = mapped(svc, turn, token)
    assert s.profile.base == Decimal(25000) and after["stale"] is False
    assert "עדכנתי את מפת האפשרויות" in turn.reply
    assert [e["result"]["today"]["net_display"] for e in after["entries"]][0] != \
           [e["result"]["today"]["net_display"] for e in before["entries"]][0]    # never the old figures


def test_a_car_change_keeps_the_map_out_of_date_until_the_model_is_known(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    turn = svc.send(turn.conversation_id, token, "התכוונתי לרכב אחר")
    _, m = mapped(svc, turn, token)
    assert m["stale"] is True and m["leaders"] == []
    turn = svc.send(turn.conversation_id, token, "קורולה 2026")
    _, m = mapped(svc, turn, token)
    assert m["stale"] is False and "CAR-07" not in [e["option_id"] for e in m["entries"]]   # the car today


def test_the_representatives_answer_completes_the_map(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (profile("מנהלת מוצר"), "יש לי קורולה קרוס", "לא יודעת", ASK):
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["option_map"] is None                                     # the copay today is unknown: no map
    qid = client.get("/api/rep/questions", headers={"X-Rep-Token": TOKEN}).json()[0]["id"]
    client.post(f"/api/rep/questions/{qid}/answer", headers={"X-Rep-Token": TOKEN}, json={"value": "300"})
    view = client.get(f"/api/conversations/{cid}").json()
    assert view["option_map"]["counts"]["eligible"] == 13 and view["option_map"]["stale"] is False
    rep = client.get(f"/api/rep/questions/{qid}", headers={"X-Rep-Token": TOKEN}).json()
    assert "option_map" not in rep and "18500" not in str(rep)            # nothing of it reaches the rep


def test_the_map_survives_a_reload_and_comes_with_every_update(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (profile("מנהלת מוצר"), CAR, ASK):
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    first = client.get(f"/api/conversations/{cid}").json()["option_map"]
    again = client.get(f"/api/conversations/{cid}").json()["option_map"]
    assert first == again and first["counts"]["eligible"] == 13
    assert client.get(f"/api/conversations/{cid}/updates").json()["option_map"] == first


# ---------- the model cannot turn the request into a choice or a number ----------

def test_with_a_model_the_request_still_maps_and_chooses_nothing(sessions):
    fake = FakeLLM()
    svc = service(sessions, fake)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR)
    fake.turn(call("select_options", options=["MG4", "ATTO 3", "EV3"]), reply="בחרתי לכם את MG4. [[state]]")
    turn = svc.send(turn.conversation_id, token, ASK)
    s, m = mapped(svc, turn, token)
    assert s.selected == [] and m["counts"]["eligible"] == 13             # refused: the employee named no car
    assert turn.reply.startswith("מיפיתי את כל 13 החלופות")               # the code's summary, not the model's


def test_unknown_inputs_never_build_a_map():
    state = ConversationState(profile=Profile(group="ג", base=Decimal(18500), commissions=None, bonus=Decimal(0),
                                              pension_percent=Decimal(6), study_fund_percent=Decimal("2.5"),
                                              credit_points=Decimal("2.75"), children=2),
                              current=CurrentCar(source="none", maintenance=Decimal(0)))
    from app.modules.conversation.tools import MissingInputs
    with pytest.raises(MissingInputs):
        build_option_map(SNAP, state)


def test_a_request_while_waiting_for_the_rep_is_kept(sessions):
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), "יש לי קורולה קרוס", "לא יודעת")
    turn = svc.send(turn.conversation_id, token, ASK)
    s = svc.view(turn.conversation_id, token).state
    assert s.map_requested and s.option_map is None and "ברגע שתגיע תשובת נציג התחבורה" in turn.reply


def test_the_model_sees_the_map_as_engine_figures_and_never_a_stale_one(sessions):
    from app.modules.conversation.turn import SYSTEM_PROMPT, state_block
    svc = service(sessions)
    turn, token = talk(svc, profile("מנהלת מוצר"), CAR, ASK)
    s = svc.view(turn.conversation_id, token).state
    block = state_block(s, SNAP)
    assert "ranked by net only" in block and "MG4 (2025): net" in block
    s.option_map.fresh = False
    assert "out of date" in state_block(s, SNAP) and "MG4 (2025): net" not in state_block(s, SNAP)
    assert 'Never call a car "the best"' in SYSTEM_PROMPT
