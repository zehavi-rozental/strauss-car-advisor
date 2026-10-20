"""הטופס המובנה {fields}: Q2 ותיקון נתונים נשמרים בקוד, בלי מודל שפה, ואותם טווחים כמו save_profile."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.conversation.expects import ChoiceInvalid
from app.modules.conversation.llm.fake import FakeLLM
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, make_client, without_cta

Q1 = "אני מנהלת מוצר, שכר בסיס 18,500"
Q2_FORM = {"commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": "2.5",
           "credit_points": "2.75", "children_under_23": 2}


def start(sessions, llm=None):
    svc = ConversationService(sessions, RulesStore(CONFIG), llm or LLMChain())
    turn, token = svc.start()
    return svc, turn.conversation_id, token


def profile(svc, cid, token):
    return svc.view(cid, token).state.profile


def q2(sessions, llm=None):
    svc, cid, token = start(sessions, llm)
    turn = svc.send(cid, token, Q1)
    assert turn.expects["field"] in ("commissions", "bonus", "pension_percent")
    return svc, cid, token


def test_full_form_saves_everything_and_asks_the_next_question(sessions):
    fake = FakeLLM()
    svc, cid, token = q2(sessions, LLMChain([fake]))
    calls_before = len(fake.calls)
    turn = svc.send_fields(cid, token, Q2_FORM)
    p = profile(svc, cid, token)
    assert (p.commissions, p.bonus, p.pension_percent, p.study_fund_percent, p.credit_points, p.children) == \
        (0, 0, 6, Decimal("2.5"), Decimal("2.75"), 2)
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and turn.reply.startswith("רשמתי:")
    assert len(fake.calls) == calls_before                       # המודל לא נקרא
    user = [m for m in svc.view(cid, token).messages if m["role"] == "user"][-1]
    assert "פנסיה 6%" in user["content"]                          # טקסט קריא לתצוגה


def test_partial_form_saves_only_what_was_sent(sessions):
    svc, cid, token = q2(sessions)
    turn = svc.send_fields(cid, token, {"commissions": 2400, "pension_percent": 6})
    p = profile(svc, cid, token)
    assert (p.commissions, p.pension_percent) == (2400, 6)
    assert p.bonus is None and p.study_fund_percent is None and p.credit_points is None and p.children is None
    # השדות שלא נשלחו נשארים חסרים (הם לא הומצאו); הזרימה ממשיכה כמו אחרי תשובה חלקית ב-Q2
    assert turn.reply.startswith("רשמתי:") and "בונוס" in turn.reply and "נקודות זיכוי" in turn.reply


@pytest.mark.parametrize("values", [{"pension_percent": 60}, {"children_under_23": 40}, {"commissions": -1},
                                    {"credit_points": 99}])
def test_out_of_range_is_rejected_and_nothing_is_saved(sessions, values):
    svc, cid, token = q2(sessions)
    before = profile(svc, cid, token).model_dump()
    with pytest.raises(ChoiceInvalid):
        svc.send_fields(cid, token, values)
    assert profile(svc, cid, token).model_dump() == before


def test_empty_or_unknown_fields_are_rejected(sessions):
    svc, cid, token = q2(sessions)
    for values in ({}, {"base_salary": 20000}):
        with pytest.raises(ChoiceInvalid):
            svc.send_fields(cid, token, values)


def test_correction_after_a_result_recalculates(sessions):
    svc, cid, token = q2(sessions)
    svc.send_fields(cid, token, Q2_FORM)
    svc.send(cid, token, "יש לי קורולה קרוס, השתתפות 300")
    view = svc.view(cid, token)
    ids = [c["id"] for c in view.expects["choices"]]
    assert ids, view.expects
    alt = next(i for i in ids if i != "OPT_OUT")
    turn = svc.send_choice(cid, token, [alt], "options")
    assert turn.stage == Stage.PRESENTED and turn.results
    first = turn.results[0]
    again = svc.send_fields(cid, token, {"bonus": 2000})
    assert profile(svc, cid, token).bonus == 2000
    assert again.results and again.results[0].run_id != first.run_id
    assert again.results[0].result["alternative"]["net"] != first.result["alternative"]["net"]


def test_api_accepts_fields_and_returns_422_for_bad_bodies(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]
    url = f"/api/conversations/{cid}/messages"
    assert client.post(url, json={"text": Q1}).status_code == 200
    ok = client.post(url, json={"fields": {"commissions": 2400}})
    assert ok.status_code == 200 and ok.json()["reply"].startswith("רשמתי:")
    assert client.post(url, json={"fields": {"pension_percent": 60}}).status_code == 422
    assert client.post(url, json={"fields": {}}).status_code == 422
    assert client.post(url, json={"fields": {"base_salary": 1}}).status_code == 422
    assert client.post(url, json={"text": "היי", "fields": {"bonus": 1}}).status_code == 422


def test_correction_while_waiting_for_the_rep_keeps_waiting(sessions):
    svc, cid, token = q2(sessions)
    svc.send_fields(cid, token, Q2_FORM)
    svc.send(cid, token, "יש לי קורולה 2026")
    turn = svc.send(cid, token, "לא יודע")
    assert turn.stage == Stage.WAITING_FOR_REP
    again = svc.send_fields(cid, token, {"children_under_23": 3})
    assert again.stage == Stage.WAITING_FOR_REP and profile(svc, cid, token).children == 3
    assert len(svc.cases.list_open()) == 1
