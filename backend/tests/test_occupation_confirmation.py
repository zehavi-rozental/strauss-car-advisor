"""תפקיד שאינו בטבלת העיסוקים לא נשמר בלי אישור — מכל מקור. עד 3 הצעות דומות מהטבלה."""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client


def service(sessions, llm=None):
    return ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())


def profile(svc, cid, token):
    return svc.view(cid, token).state


def test_llm_work_pattern_is_not_saved_and_the_bot_asks(sessions):
    """מהערכה חיה: המודל שלח occupation="במשמרות". אין דומה בטבלה → שאלת אישור, ושום תפקיד לא נשמר."""
    fake = FakeLLM().turn(call("save_profile", occupation="במשמרות", base_salary=18500, pension_percent=6))
    svc = service(sessions, LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "שכר בסיס 18,500 ופנסיה 6%, ואני במשמרות")
    s = profile(svc, turn.conversation_id, token)
    assert (s.profile.occupation, s.profile.occupation_text, s.profile.occupation_unknown) == (None, None, False)
    assert (s.profile.base, s.profile.pension_percent) == (18500, 6)
    assert s.pending_occupation.text == "במשמרות" and s.pending_occupation.suggestions == []
    assert turn.reply.endswith('לא מצאתי את "במשמרות" בטבלת העיסוקים. זה התפקיד שלך? אם לא, כתבו את התפקיד שלך.')
    assert turn.source == "template"
    assert turn.expects["field"] == "occupation_confirmation"
    assert [c["id"] for c in turn.expects["choices"]] == ["yes"]


def test_near_miss_suggests_the_table_occupation(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "מנהלת מוצרים")
    assert turn.reply.endswith('לא מצאתי את "מנהלת מוצרים" בטבלת העיסוקים. התכוונת לאחד מאלה: מנהל/ת מוצר? '
                               "או כתבו את התפקיד שלך.")
    assert [c["id"] for c in turn.expects["choices"]] == ["מנהל/ת מוצר", "yes"]
    assert turn.expects["choices"][-1]["label"] == "כן, זה התפקיד שלי"
    assert profile(svc, turn.conversation_id, token).profile.occupation is None


def test_choosing_a_suggestion_saves_it_with_its_group(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "מנהלת מוצרים"})
    body = client.post(f"/api/conversations/{cid}/messages", json={"choice": "מנהל/ת מוצר"}).json()
    p = client.get(f"/api/conversations/{cid}").json()["profile"]
    assert (p["occupation"], p["group"], p["occupation_unknown"]) == ("מנהל/ת מוצר", "ג", False)
    assert body["expects"]["field"] == "base"


def test_suggestion_by_number_or_its_name_in_text(sessions):
    svc = service(sessions)
    for answer in ("1", "מנהל מוצר"):
        turn, token = svc.start()
        svc.send(turn.conversation_id, token, "מנהלת מוצרים")
        turn = svc.send(turn.conversation_id, token, answer)
        s = profile(svc, turn.conversation_id, token)
        assert (s.profile.occupation, s.pending_occupation) == ("מנהל/ת מוצר", None)


def test_yes_saves_the_unknown_occupation_and_asks_the_group(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, "קופאית")
    turn = svc.send(turn.conversation_id, token, "כן")
    p = profile(svc, turn.conversation_id, token).profile
    assert (p.occupation, p.occupation_text, p.occupation_unknown) == (None, "קופאית", True)
    assert "קבוצת הזכאות" in turn.reply and turn.stage == Stage.COLLECT_PROFILE


def test_no_or_another_title_replaces_the_question(sessions):
    svc = service(sessions)
    turn, token = svc.start()
    svc.send(turn.conversation_id, token, "במשמרות")
    turn = svc.send(turn.conversation_id, token, "לא")
    s = profile(svc, turn.conversation_id, token)
    assert s.pending_occupation is None and s.profile.occupation_text is None
    assert "מה התפקיד שלך" in turn.reply
    svc.send(turn.conversation_id, token, "במשמרות")
    turn = svc.send(turn.conversation_id, token, "מפתחת תוכנה")
    s = profile(svc, turn.conversation_id, token)
    assert (s.profile.occupation, s.pending_occupation) == ("מפתח/ת תוכנה", None)


def test_the_llm_cannot_confirm_an_unknown_occupation_itself(sessions):
    """"כן" נענה בקוד (בלי מודל); גם קריאה של המודל עם שם לא מהטבלה רק פותחת שוב את השאלה."""
    fake = FakeLLM().turn(call("save_profile", occupation="אסטרונאוטית"))
    svc = service(sessions, LLMChain([fake]))
    turn, token = svc.start()
    turn = svc.send(turn.conversation_id, token, "אני אסטרונאוטית")
    before = len(fake.calls)
    turn = svc.send(turn.conversation_id, token, "כן")
    assert len(fake.calls) == before                                  # האישור — בלי המודל
    assert profile(svc, turn.conversation_id, token).profile.occupation_text == "אסטרונאוטית"
