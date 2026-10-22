"""The "גלו לי את כל האפשרויות" button may be offered only when the option map can really be built (QA 22/10).

Found: the button showed as soon as a choice of cars was among the open fields (with the car question, before
the salary leftovers and today's car were known), and a click only got "חסרים לי עוד: …". The view now says
whether the map is ready (`map_ready`) and what it still needs (`map_missing`), from the same conditions the
server builds the map on (`stage.map_ready`, used by `Agent._resolve_map`)."""
from __future__ import annotations

from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.conversation.stage import map_missing, map_ready
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client

TOKEN = "rep-secret"
PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
           "2.75 נקודות זיכוי, 2 ילדים")
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


def view(client, cid):
    r = client.get(f"/api/conversations/{cid}")
    assert r.status_code == 200
    return r.json()


def say(client, cid, text):
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    assert r.status_code == 200
    return r.json()


def test_the_map_is_not_ready_until_every_value_and_todays_car_are_known(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    v = view(client, cid)
    assert v["map_ready"] is False and "שכר בסיס" in v["map_missing"]            # the greeting
    say(client, cid, "מנהלת מוצר, שכר בסיס 18,500")
    assert view(client, cid)["map_ready"] is False                               # Q2
    say(client, cid, "אין עמלות, בונוס 2,000, פנסיה 6%")                        # the screenshot: Q3 opens
    v = view(client, cid)
    assert any(f["field"] == "options" for f in v["expects"]["fields"])           # a choice of cars is open…
    assert v["map_ready"] is False                                               # …but the map cannot be built
    assert {"קרן השתלמות", "נקודות זיכוי", "ילדים"} <= set(v["map_missing"])
    say(client, cid, "קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")
    assert view(client, cid)["map_ready"] is False                               # today's car is still missing
    say(client, cid, CAR)
    v = view(client, cid)
    assert (v["map_ready"], v["map_missing"]) == (True, [])
    say(client, cid, "השווה את כל האפשרויות שלי")
    v = view(client, cid)
    assert v["option_map"] is not None and v["option_map"]["stale"] is False      # the promise is kept


def test_updates_carry_the_readiness_too(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (PROFILE, CAR):
        say(client, cid, text)
    u = client.get(f"/api/conversations/{cid}/updates?after=0").json()
    assert (u["map_ready"], u["map_missing"]) == (True, [])


def test_a_blocking_question_to_the_representative_keeps_it_not_ready(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in (PROFILE, "יש לי קורולה קרוס", "לא יודעת"):                   # today's copay is unknown
        say(client, cid, text)
    assert view(client, cid)["map_ready"] is False


def test_the_view_and_the_map_builder_agree(sessions):
    """map_ready is exactly when _resolve_map builds: asked at every step, the map exists iff it was ready."""
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    turn, token = svc.start()
    for text in ("מנהלת מוצר, שכר בסיס 18,500", "אין עמלות, בונוס 2,000, פנסיה 6%",
                 "קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים", CAR):
        turn = svc.send(turn.conversation_id, token, text)
        ready = map_ready(svc.view(turn.conversation_id, token).state)
        asked = svc.send(turn.conversation_id, token, "השווה את כל האפשרויות שלי")
        state = svc.view(asked.conversation_id, token).state
        assert (state.option_map is not None) == ready, text
        assert ready == (map_missing(state) == [])


def test_a_representatives_answer_that_completes_the_map_shows_it(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    cid = client.post("/api/conversations").json()["conversation_id"]
    # the wait: a stated group the occupations table contradicts (C4); the representative's group completes it
    for text in ("קבוצה ב, שכר בסיס 18,500", "אני מנהלת מוצר",
                 "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
                 "יש לי קורולה קרוס, משלמת 300", "השווה את כל האפשרויות שלי"):
        say(client, cid, text)
    qid = client.get("/api/rep/questions", headers={"X-Rep-Token": TOKEN}).json()[0]["id"]
    client.post(f"/api/rep/questions/{qid}/answer", headers={"X-Rep-Token": TOKEN}, json={"value": "ג"})
    texts = [m["content"] for m in view(client, cid)["messages"] if m["role"] != "user"]
    # built for the first time by the answer: the map itself is presented, not only "עדכנתי"
    assert any("מיפיתי את כל" in t for t in texts[-2:]), texts[-3:]
