"""Every data edit, both ways ("הנתונים שלי" and the chat), after a comparison and the options map (15/11).

For each field — base salary, a contribution, the group, today's car, today's copay — and each path:
saved for real; one reply, one "עדכנתי" with the old and the new value; every result recalculated (not stale, a new
results version); the chat's figures are the cards', and the map's figure for each compared car is the card's; the same
after a reload; the conversation goes on (a ranking after it). A refused value says no success and changes nothing.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.templates import money
from app.shared.money import to_shekel

from .conftest import make_client

PROFILE = ("שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, "
           "קבוצה ב")

# (path, field, message or form body, saved check, what the "עדכנתי" line says)
CASES = [
    ("form", "base", {"fields": {"base_salary": 20000}}, ("profile", "base", "20000"), ("20,000 ₪", "18,500 ₪")),
    ("chat", "base", "טעיתי, השכר שלי בעצם 20 אלף", ("profile", "base", "20000"), ("20,000 ₪", "18,500 ₪")),
    ("form", "pension", {"fields": {"pension_percent": 7}}, ("profile", "pension_percent", "7"), ("7%", "6%")),
    ("chat", "pension", "בעצם הפנסיה שלי 7%", ("profile", "pension_percent", "7"), ("7%", "6%")),
    ("form", "group", {"fields": {"eligibility_group": "ג"}}, ("profile", "group", "ג"), ("קבוצה ג", "ב")),
    ("chat", "group", "רגע, אני בקבוצה ג", ("profile", "group", "ג"), ("קבוצה ג", "ב")),
    ("form", "car", {"fields": {"current_car": "CAR-05"}}, ("car", "label", "NIRO (2025)"),   # the list's id
     ("NIRO (2025)", "COROLLA CROSS (2025)")),
    ("chat", "car", "טעיתי, הרכב שלי בעצם נירו", ("car", "label", "NIRO (2025)"),
     ("NIRO (2025)", "COROLLA CROSS (2025)")),
    ("form", "copay", {"fields": {"current_copay": 400}}, ("copay", None, "400"), ("400 ₪", "300 ₪")),
    ("chat", "copay", "אני משלמת 400 ולא 300", ("copay", None, "400"), ("400 ₪", "300 ₪")),
]


def setup(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(body):
        r = client.post(f"/api/conversations/{cid}/messages", json=body if isinstance(body, dict) else {"text": body})
        return r

    def view():
        return client.get(f"/api/conversations/{cid}").json()

    for text in (PROFILE, "יש לי קורולה קרוס", "300", "תשווה בין MG4 ל-NIRO", "תראה לי את כל האפשרויות"):
        assert say(text).status_code == 200
    return client, cid, say, view


def saved_value(v, where, key):
    if where == "profile":
        return v["profile"][key]
    if where == "car":
        return v["current_car"]["label"]
    return v["today_copay"]


def nets(v):
    return {r["label"]: r["result"]["alternative"]["net_display"] for r in v["results"]}


@pytest.mark.parametrize(("path", "field", "body", "saved", "said"), CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_an_edit_is_saved_said_once_recalculated_consistent_and_kept(engine, path, field, body, saved, said):
    client, cid, say, view = setup(engine)
    before = view()
    count = len(before["messages"])
    r = say(body)
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    v = view()

    # saved for real
    assert saved_value(v, saved[0], saved[1]) == saved[2]
    # one reply, one update line, old and new
    assert [m["role"] for m in v["messages"][count:]] == ["user", "assistant"]
    # the value's confirmation (the map's own "עדכנתי את מפת האפשרויות…" says the map was rebuilt, not the value)
    update = [ln for ln in reply.splitlines() if ln.startswith("עדכנתי") and "במקום" in ln]
    assert len(update) == 1, reply
    assert said[0] in update[0] and said[1] in update[0], update[0]
    # recalculated: a new results version, nothing stale, both compared cars still there
    assert not v["results_stale"] and v["results_version"] > before["results_version"]
    # NIRO as today's car is no longer an alternative to itself: only MG4 stays compared
    expected = {"MG4 (2025)"} if field == "car" else {"MG4 (2025)", "NIRO (2025)"}
    assert {r["label"] for r in v["results"]} == expected
    # the chat's figures are the cards': no per-car list (#9, 27/11); a car named with the highest net has its net
    assert "הנטו החודשי המשוער:" not in reply
    for label, net in nets(v).items():
        if f"{label} משאיר את הנטו הגבוה ביותר" in reply:
            assert f"{label} משאיר את הנטו הגבוה ביותר: {money(to_shekel(Decimal(net)))} בחודש" in reply
    # the map: rebuilt or out of date, never an old ranking shown as current; when current, its figures are the cards'
    m = v["option_map"]
    if m is not None and not m["stale"]:
        by_label = {e["label"]: (e["result"].get("alternative") or {}).get("net_display") for e in m["entries"]}
        for label, net in nets(v).items():
            if label in by_label:
                assert by_label[label] == net, (label, by_label[label], net)
        assert m["counts"]["eligible"] == m["counts"]["cars"] + len(m["counts"]["others"])
    # the same after a reload
    again = view()
    assert saved_value(again, saved[0], saved[1]) == saved[2] and nets(again) == nets(v)
    # the conversation goes on
    ranked = say("מה הכי משתלם?")
    assert ranked.status_code == 200 and "הרכבים שאתם זכאים להם" in ranked.json()["reply"]
    assert len(view()["results"]) == 3


@pytest.mark.parametrize("body", [{"fields": {"base_salary": 50}}, {"fields": {"pension_percent": 40}}],
                         ids=["form-salary-50", "form-pension-40"])
def test_a_refused_form_value_says_no_success_and_changes_nothing(engine, body):
    client, cid, say, view = setup(engine)
    before = view()
    r = say(body)
    assert r.status_code == 422
    v = view()
    assert len(v["messages"]) == len(before["messages"])            # nothing said, nothing saved
    assert v["profile"] == before["profile"] and nets(v) == nets(before) and not v["results_stale"]


@pytest.mark.parametrize("text", ["בעצם הפנסיה שלי 40%", "טעיתי, השכר שלי 50"])
def test_a_refused_chat_value_says_no_success_and_keeps_the_results(engine, text):
    client, cid, say, view = setup(engine)
    before = view()
    reply = say(text).json()["reply"]
    v = view()
    assert "עדכנתי" not in reply and "החישובים עודכנו" not in reply
    assert v["profile"]["pension_percent"] == before["profile"]["pension_percent"]
    assert v["profile"]["base"] == before["profile"]["base"]
    assert nets(v) == nets(before) and not v["results_stale"]
