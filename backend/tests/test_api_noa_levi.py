"""נועה לוי (E-001) מקצה לקצה דרך ה-API: קורולה קרוס → ATTO 3 = ‎+661 ₪ נטו."""
from __future__ import annotations

from sqlalchemy import select

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.models import CalculationRunRow, ConfigSnapshotRow, MessageRow
from app.modules.conversation.public import LLMChain

from .conftest import make_client

PENDING = "השתתפות ב-ATTO 3 (2025) לפי הקטלוג — ממתינה לאישור נציג"


def _assert_noa_result(body, sessions):
    assert body["stage"] == "PRESENTED"
    [r] = body["results"]
    assert r["label"] == "ATTO 3 (2025)"
    assert r["status"] == "OK" and r["eligible"] is True
    assert r["delta_display"] == "661"
    assert r["today"]["net_display"] == "11032"
    assert r["alternative"]["net_display"] == "11693"
    assert PENDING in r["assumptions"]

    with sessions() as s:
        runs = s.scalars(select(CalculationRunRow)).all()
        assert len(runs) == 1 and str(runs[0].id) == r["run_id"]
        snap = s.get(ConfigSnapshotRow, runs[0].config_snapshot_id)
        assert snap is not None and snap.strauss_data_xlsx


def test_noa_levi_with_llm(engine, sessions):
    fake = FakeLLM()
    client = make_client(engine, llm=LLMChain([fake]))

    r = client.post("/api/conversations")
    assert r.status_code == 201 and "sca_session" in r.cookies
    cid = r.json()["conversation_id"]

    fake.turn(call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                   pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2),
              reply="תודה! איזה רכב יש לך היום?")
    body = client.post(f"/api/conversations/{cid}/messages", json={
        "text": "אני מנהלת מוצר, ברוטו 18,500, בלי עמלות ובונוסים, פנסיה 6%, השתלמות 2.5%, "
                "2.75 נקודות זיכוי, 2 ילדים"}).json()
    assert body["stage"] == "COLLECT_CURRENT_CAR"
    assert body["source"] == "template"      # השאלה הבאה — תבנית, בלי קריאת ניסוח

    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300),
              reply="מצאתי את COROLLA CROSS (2025). לאיזה רכב להשוות?")
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "קורולה קרוס, משתתפת 300"}).json()
    assert body["stage"] == "CHOOSE_OPTIONS"

    fake.turn(call("calculate", options=["ATTO 3"]),
              reply="הנה ההשוואה שביקשת, לפי כללי החברה.")
    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "ATTO 3"}).json()
    _assert_noa_result(body, sessions)
    assert body["source"] == "llm" and body["reply"].startswith("הנה ההשוואה שביקשת")
    assert "+661 ₪" in body["reply"] and "11,693 ₪" in body["reply"]     # שורות התוצאה מהתבנית

    # הכלים שהוצעו למודל תלויים בשלב
    tool_sets = [c.tools for c in fake.calls if c.tools]
    assert tool_sets[0] == ["save_profile"]
    assert "calculate" in tool_sets[-1]

    state = client.get(f"/api/conversations/{cid}").json()
    assert state["stage"] == "PRESENTED"
    assert state["current_car"]["catalog_code"] == "CAR-09"
    assert len(state["messages"]) == 7   # פתיחה + 3 × (עובד + בוט)


def test_noa_levi_fallback_mode(engine, sessions):
    client = make_client(engine)   # בלי מודל שפה: שאלה אחת בכל פעם
    cid = client.post("/api/conversations").json()["conversation_id"]
    body = None
    for text in ["מנהלת מוצר", "18,500", "אין", "אין", "6%", "2.5", "2.75", "2",
                 "קורולה קרוס", "300", "אטו 3"]:      # שמות בעברית: כינויים מ-extensions.yaml
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
        assert body["source"] == "template"
    _assert_noa_result(body, sessions)
    assert "+661 ₪" in body["reply"] and "11,693 ₪" in body["reply"]

    with sessions() as s:
        sources = {m.source for m in s.scalars(select(MessageRow))}
    assert sources == {"user", "template"}
