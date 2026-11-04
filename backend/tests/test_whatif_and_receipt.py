"""A real change or only a check, the "what changed" card, one value edited on its own, and what is known. Synthetic data only;
the figures come from the engine in the same run, never written here as expected constants except where the review pinned them."""
from __future__ import annotations

import pytest

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import LLMChain

from .conftest import build_engine, make_client

Q2 = {"base_salary": 18000, "commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
      "credit_points": 2.25, "children_under_23": 2}


def start(client):
    cid = client.post("/api/conversations").json()["conversation_id"]
    return cid, lambda body: client.post(f"/api/conversations/{cid}/messages", json=body).json()


def at_cars(client, q2=Q2, group="קבוצה ג"):
    cid, send = start(client)
    send({"text": group})
    send({"fields": q2})
    send({"text": "קורולה 2026"})
    send({"choice": "catalog:CAR-07", "field": "copay"})
    return cid, send


def at_ranked(client, q2=Q2):
    cid, send = at_cars(client, q2)
    send({"text": "הכי משתלם"})
    return cid, send


def view(client, cid):
    return client.get(f"/api/conversations/{cid}").json()


def deltas(v):
    return {r["option_id"]: r["result"]["delta_display"] for r in v["results"]}


def item(v, key):
    return next(i for i in v["facts"]["items"] if i["key"] == key)


WHAT_IFS = ["מה יקרה אם ארוויח 20,000?", "מה אם השכר שלי יעלה ל-20,000?", "ואם הייתי מרוויח 20,000?",
            "אם אקבל העלאה ל-20,000 כמה יישאר לי?", "נניח שהשכר שלי 20,000", "אם השכר שלי 20,000 מה הנטו?"]


# ---------- a check never changes what is saved ----------

@pytest.mark.parametrize("text", WHAT_IFS)
def test_a_what_if_saves_nothing_and_shows_the_engines_figures(engine, text):
    client = make_client(engine)
    cid, send = at_ranked(client)
    before = view(client, cid)
    turn = send({"text": text})
    after = view(client, cid)
    assert after["profile"] == before["profile"] and after["results"] == before["results"]
    assert after["data_version"] == before["data_version"] and after["selected"] == before["selected"]
    assert turn["reply"].startswith("בדיקה בלבד: הנתונים שלכם לא השתנו.")
    card = turn["card"]
    assert card["kind"] == "scenario" and card["apply"]["values"] == {"base_salary": "20000"}
    # the check's figures are the engine's: the same as really making the change, in another conversation
    real = make_client(build_engine())
    cid2, send2 = at_ranked(real)
    send2({"fields": {"base_salary": 20000}})
    assert {o["option_id"]: o["after"]["delta"] for o in card["options"]} == deltas(view(real, cid2))
    assert card["today_net"]["after"] == view(real, cid2)["results"][0]["result"]["today"]["net_display"]


def test_the_review_numbers_of_the_18000_to_20000_check(engine):
    client = make_client(engine)
    _, send = at_ranked(client)
    card = send({"text": "מה יקרה אם ארוויח 20,000?"})["card"]
    assert card["today_net"] == {"before": "10703", "after": "11602"}
    mg4 = next(o for o in card["options"] if o["option_id"] == "CAR-06")
    assert mg4["before"]["delta"] == "979" and mg4["after"]["delta"] == "1047"
    assert card["order_changed"] is False


@pytest.mark.parametrize("text, field", [("מה אם הפנסיה 7%?", "pension_percent"), ("ואם יהיו לי 3 ילדים?", "children")])
def test_a_what_if_on_other_values_saves_nothing(engine, text, field):
    client = make_client(engine)
    cid, send = at_ranked(client)
    before = view(client, cid)["profile"][field]
    turn = send({"text": text})
    assert view(client, cid)["profile"][field] == before and turn["card"]["kind"] == "scenario"


def test_a_what_if_on_a_car_calculates_it_without_changing_the_comparison(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-06"], "field": "options"})
    turn = send({"text": "מה קורה אם אבחר ב-NIRO?"})
    assert view(client, cid)["selected"] == ["CAR-06"]
    assert any(o["option_id"] == "CAR-05" for o in turn["card"]["options"])
    assert any(f["field"] == "scenario_apply" for f in turn["expects"]["fields"])
    send({"choice": "apply", "field": "scenario_apply"})
    assert view(client, cid)["selected"] == ["CAR-06", "CAR-05"]


def test_a_what_if_before_the_details_are_known_saves_nothing_and_says_what_is_missing(engine):
    client = make_client(engine)
    cid, send = start(client)
    turn = send({"text": "מה יקרה אם ארוויח 20,000?"})
    assert view(client, cid)["profile"]["base"] is None
    assert "כדי לבדוק מה יקרה, חסרים לי עוד" in turn["reply"] and "לא רשמתי" in turn["reply"]


def test_a_what_if_out_of_range_says_so(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    before = view(client, cid)["profile"]
    turn = send({"text": "מה אם הפנסיה 34%?"})
    assert view(client, cid)["profile"] == before and "מחוץ לטווח" in turn["reply"]


# ---------- the buttons under a check ----------

def test_saving_a_check_is_the_same_change_as_any_other_and_keeping_it_changes_nothing(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    send({"text": "מה יקרה אם ארוויח 20,000?"})
    kept = send({"choice": "dismiss", "field": "scenario_apply"})
    assert view(client, cid)["profile"]["base"] == "18000" and "נשארו" in kept["reply"]

    send({"text": "מה יקרה אם ארוויח 20,000?"})
    saved = send({"choice": "apply", "field": "scenario_apply"})
    assert view(client, cid)["profile"]["base"] == "20000"
    assert "עדכנתי: שכר בסיס 20,000 ₪ במקום 18,000 ₪" in saved["reply"] and saved["card"]["kind"] == "receipt"


def test_typed_answers_to_a_check(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    send({"text": "מה יקרה אם ארוויח 20,000?"})
    send({"text": "לעדכן"})
    assert view(client, cid)["profile"]["base"] == "20000"


def test_a_new_subject_drops_the_offer_of_a_check(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    send({"text": "מה יקרה אם ארוויח 20,000?"})
    turn = send({"text": "מה זה נקודות זיכוי?"})
    assert all(f["field"] != "scenario_apply" for f in turn["expects"]["fields"])
    assert view(client, cid)["profile"]["base"] == "18000"


# ---------- a real change ----------

@pytest.mark.parametrize("text", ["אני מרוויח עכשיו 20,000", "השכר שלי עלה ל-20,000", "קיבלתי העלאה ל-20,000",
                                  "תעדכן את השכר ל-20,000", "השכר שלי 20,000", "המשכורת עלתה ל-20 אלף"])
def test_a_declared_change_is_saved_in_template_mode_too(engine, text):
    client = make_client(engine)
    cid, send = at_ranked(client)
    turn = send({"text": text})
    assert view(client, cid)["profile"]["base"] == "20000", turn["reply"]
    assert "עדכנתי: שכר בסיס 20,000 ₪ במקום 18,000 ₪" in turn["reply"]


# ---------- in doubt, asked ----------

@pytest.mark.parametrize("text", ["20,000", "20000", "20 אלף", "השכר שלי 20,000?"])
def test_a_value_with_no_clear_intent_is_asked_and_nothing_is_saved(engine, text):
    client = make_client(engine)
    cid, send = at_ranked(client)
    turn = send({"text": text})
    assert view(client, cid)["profile"]["base"] == "18000"
    assert "או רק לבדוק" in turn["reply"]
    assert [f["field"] for f in turn["expects"]["fields"]] == ["value_intent"]


@pytest.mark.parametrize("answer, base, words", [("update", "20000", "עדכנתי"), ("what_if", "18000", "בדיקה בלבד")])
def test_the_two_answers_to_the_question(engine, answer, base, words):
    client = make_client(engine)
    cid, send = at_ranked(client)
    send({"text": "20,000"})
    turn = send({"choice": answer, "field": "value_intent"})
    assert view(client, cid)["profile"]["base"] == base and words in turn["reply"]


def test_a_bare_number_that_answers_a_question_is_not_asked_back(engine):
    client = make_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה ג"})
    turn = send({"text": "20,000"})
    assert view(client, cid)["profile"]["base"] == "20000" and "או רק לבדוק" not in turn["reply"]


# ---------- the model cannot change data on a hypothetical ----------

def with_model(client, fake):
    client.app.state.ctx.service.agent.llm = LLMChain([fake])


def test_a_model_that_saves_on_an_unlisted_hypothetical_is_refused_when_the_message_only_asks(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    # no listed cue: the whole message is one question, about a value that is saved
    with_model(client, FakeLLM().turn(call("save_profile", base_salary=20000), reply="בסדר."))
    send({"text": "כמה יישאר לי בנטו עם שכר של 20,000?"})
    assert view(client, cid)["profile"]["base"] == "18000"


def test_try_scenario_from_the_model_runs_the_check_on_a_copy(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    with_model(client, FakeLLM().turn(call("try_scenario", base_salary=20000)))
    turn = send({"text": "ובמצב של 20,000 ברוטו בחודש?"})
    assert view(client, cid)["profile"]["base"] == "18000"
    assert turn["card"]["kind"] == "scenario" and turn["reply"].startswith("בדיקה בלבד")


def test_try_scenario_needs_the_number_in_the_message(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    with_model(client, FakeLLM().turn(call("try_scenario", base_salary=99000), reply="לא ברור."))
    turn = send({"text": "ומה המצב אם השכר יהיה גבוה יותר?"})
    assert view(client, cid)["profile"]["base"] == "18000" and (turn.get("card") or {}).get("kind") != "scenario"


# ---------- the "what changed" card ----------

def test_the_receipt_of_18000_to_20000(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    turn = send({"fields": {"base_salary": 20000}})
    card = turn["card"]
    assert card["kind"] == "receipt"
    assert card["changes"] == [{"field": "base_salary", "label": "שכר בסיס", "after": "20,000 ₪", "before": "18,000 ₪"}]
    assert card["today_net"] == {"before": "10703", "after": "11602"}
    assert {o["option_id"]: (o["before"]["delta"], o["after"]["delta"]) for o in card["options"]} == {
        "CAR-06": ("979", "1047"), "CAR-03": ("960", "1022"), "CAR-08": ("751", "797")}
    assert card["order_changed"] is False
    assert "הנטו הגבוה ביותר" in turn["reply"]       # the chat says what to take from it; the figures are on the card


def test_the_receipt_says_when_the_cars_changed(engine):
    client = make_client(engine)
    cid, send = at_ranked(client, {**Q2, "base_salary": 9000})
    turn = send({"fields": {"base_salary": 45000}})
    assert turn["card"]["order_changed"] is True
    assert [a["option_id"] for a in turn["card"]["added"]] == ["CAR-01"]
    assert [a["option_id"] for a in turn["card"]["left"]] == ["CAR-08"]
    assert turn["card"]["order_changed"] is True


def test_a_message_keeps_the_results_it_reported_so_an_older_one_can_be_marked(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    first = view(client, cid)
    reported = [m for m in first["messages"] if m["results_version"] is not None]
    assert reported and reported[-1]["results_version"] == first["results_version"]
    send({"fields": {"base_salary": 20000}})
    later = view(client, cid)
    assert later["results_version"] == first["results_version"] + 1
    versions = [m["results_version"] for m in later["messages"] if m["results_version"] is not None]
    assert versions[-1] == later["results_version"] and versions[0] < later["results_version"]
    card = [m["card"] for m in later["messages"] if m["card"]]
    assert card and card[-1]["kind"] == "receipt"          # kept with its message, in the view too


def test_the_updates_feed_carries_the_versions_and_the_card(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    send({"fields": {"base_salary": 20000}})
    u = client.get(f"/api/conversations/{cid}/updates").json()
    assert u["results_version"] >= 2 and u["data_version"] >= 1 and u["facts"]["version"] == u["data_version"]
    assert any(m["card"] for m in u["messages"])


# ---------- one value edited on its own ----------

def test_a_form_refuses_an_edit_made_on_an_older_screen(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    version = view(client, cid)["data_version"]
    assert client.post(f"/api/conversations/{cid}/messages",
                       json={"fields": {"base_salary": 20000}, "base_version": version}).status_code == 200
    stale = client.post(f"/api/conversations/{cid}/messages",
                        json={"fields": {"base_salary": 21000}, "base_version": version})
    assert stale.status_code == 409 and view(client, cid)["profile"]["base"] == "20000"


@pytest.mark.parametrize("fields, key, display", [
    ({"credit_points": 3}, "credit_points", "3"), ({"pension_percent": 5}, "pension_percent", "5%"),
    ({"current_copay": 300}, "current_copay", "300 ₪"), ({"study_fund_percent": 2}, "study_fund_percent", "2%"),
])
def test_one_value_edited_recalculates_and_the_facts_follow(engine, fields, key, display):
    client = make_client(engine)
    cid, send = at_ranked(client)
    before = deltas(view(client, cid))
    turn = send({"fields": fields})
    assert turn.get("detail") is None and turn["card"]["kind"] == "receipt"
    v = view(client, cid)
    assert item(v, key)["display"] == display and item(v, key)["source"] == "form"
    if key == "current_copay":
        assert deltas(v) != before


def test_the_group_can_be_edited_when_no_role_decides_it(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    turn = send({"fields": {"eligibility_group": "א"}})
    v = view(client, cid)
    assert v["profile"]["group"] == "א" and "קבוצה א במקום ג" in turn["reply"]
    assert "כל 4 הרכבים" in turn["reply"]


def test_a_group_the_role_decides_is_refused_with_the_reason(engine):
    client = make_client(engine)
    cid, send = at_cars(client, group="אני מנהלת מוצר")
    r = client.post(f"/api/conversations/{cid}/messages", json={"fields": {"eligibility_group": "א"}})
    assert r.status_code == 422 and "נקבעת לפי התפקיד" in r.json()["errors"]["eligibility_group"]
    assert item(view(client, cid), "group")["edit"] is None


def test_todays_car_edited_on_its_own(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-01"], "field": "options"})
    turn = send({"fields": {"current_car": "CAR-05"}})
    v = view(client, cid)
    assert v["today_car"].startswith("NIRO") and "NIRO" in turn["reply"]
    assert v["today_copay_source"] == "carried" and item(v, "current_copay")["status"] == "assumption"


@pytest.mark.parametrize("fields, field", [({"current_copay": 350000}, "current_copay"), ({"current_car": "NOPE"}, "current_car"),
                                           ({"current_use_value": 3000}, "current_use_value"), ({"occupation": "x"}, "occupation")])
def test_an_edit_outside_the_rules_saves_nothing_and_names_the_field(engine, fields, field):
    client = make_client(engine)
    cid, send = at_cars(client)
    before = view(client, cid)
    r = client.post(f"/api/conversations/{cid}/messages", json={"fields": fields})
    assert r.status_code == 422 and field in r.json()["errors"]
    assert view(client, cid)["data_version"] == before["data_version"]


# ---------- what is known ----------

def test_the_facts_say_what_is_known_missing_and_where_it_came_from(engine):
    client = make_client(engine)
    cid, send = start(client)
    send({"text": "קבוצה ג"})
    v = view(client, cid)
    assert item(v, "base_salary")["status"] == "missing" and "שכר בסיס" in v["facts"]["missing"]
    assert item(v, "group")["display"] == "קבוצה ג"
    send({"fields": {**Q2}})
    v = view(client, cid)
    assert item(v, "base_salary")["display"] == "18,000 ₪" and item(v, "base_salary")["source_label"] == "הקלדתם"
    send({"text": "קורולה 2026"})
    v = view(client, cid)
    assert item(v, "current_use_value")["source"] == "catalog" and item(v, "current_use_value")["edit"] is None
    assert "טעונה אימות" in " ".join(v["facts"]["assumptions"])     # the rules version is marked as needing verification
    send({"choice": "catalog:CAR-07", "field": "copay"})
    send({"text": "הכי משתלם"})
    v = view(client, cid)
    assert item(v, "today_net")["status"] == "calculated" and item(v, "today_net")["display"] == "10,703 ₪"
    assert 0 < len(v["facts"]["strip"]) <= 4


# ---------- words about the conversation ----------

@pytest.mark.parametrize("model", [False, True])
def test_what_is_saved_is_answered_from_the_state_in_both_modes(engine, model):
    client = make_client(engine)
    _, send = at_ranked(client)
    if model:
        with_model(client, FakeLLM())
    for text in ("מה השכר שרשמת?", "אילו נתונים יש לך עליי?", "מה הנתונים שלי?", "תראה לי מה רשמת"):
        reply = send({"text": text})["reply"]
        assert "18,000" in reply and "רשום אצלי" in reply, text


def test_a_pause_is_answered_and_the_results_stay(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    before = deltas(view(client, cid))
    reply = send({"text": "אפשר להמשיך אחר כך?"})["reply"]
    assert reply.startswith("כן.") and "איזה רכבים להשוות" not in reply and deltas(view(client, cid)) == before


def test_after_results_a_vague_message_keeps_them_and_does_not_ask_to_choose_cars(engine):
    client = make_client(engine)
    cid, send = at_ranked(client)
    reply = send({"text": "אממ"})["reply"]
    assert "התוצאות נשארות" in reply and "איזה רכבים להשוות" not in reply


# ---------- the comparison in words ----------

@pytest.mark.parametrize("text, expected", [
    ("תוסיף גם את NIRO", ["CAR-06", "CAR-05"]), ("תוריד את MG4", ["CAR-01"]),
    ("במקום MG4 תבדוק את NIRO", ["CAR-05", "CAR-01"]),
])
def test_the_comparison_changes_in_words_and_is_recalculated(engine, text, expected):
    client = make_client(engine)
    cid, send = at_cars(client)
    start_with = ["CAR-06"] if "תוסיף" in text else ["CAR-06", "CAR-01"]
    send({"choices": start_with, "field": "options"})
    with_model(client, FakeLLM())
    turn = send({"text": text})
    v = view(client, cid)
    assert v["selected"] == expected and [r["option_id"] for r in v["results"]] == expected, turn["reply"]


def test_a_fourth_car_is_asked_not_dropped(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-06", "CAR-01", "CAR-03"], "field": "options"})
    turn = send({"text": "תוסיף גם את NIRO"})
    assert view(client, cid)["selected"] == ["CAR-06", "CAR-01", "CAR-03"] and "עד 3" in turn["reply"]


def test_removing_the_only_car_asks_again(engine):
    client = make_client(engine)
    cid, send = at_cars(client)
    send({"choices": ["CAR-06"], "field": "options"})
    turn = send({"text": "תוריד את MG4"})
    v = view(client, cid)
    assert v["selected"] == [] and v["results"] == [] and turn["expects"]["field"] == "options"


def test_one_car_on_screen_is_not_called_the_highest():
    from app.modules.conversation.receipt import receipt_lines
    one = {"today_net": {"before": None, "after": None}, "ranked": None, "order_changed": False,
           "options": [{"label": "ATTO 3", "after": {"delta": "656"}}]}
    assert receipt_lines(one) == ["הרכב שבדקתם: ATTO 3, תוספת של 656 ₪ בחודש."]
    two = dict(one, options=one["options"] + [{"label": "MG4", "after": {"delta": "-10"}}])
    assert receipt_lines(two)[0].startswith("הנטו הגבוה ביותר: ATTO 3")
