"""אין טרנזקציה פתוחה בזמן קריאות למודל השפה, ושורת גרסת ההגדרות נוצרת מחוץ לתור."""
from __future__ import annotations

from sqlalchemy import func, select

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.models import CalculationRunRow, ConfigSnapshotRow
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.conversation.repository import ConversationRepository
from app.modules.rules.public import RulesStore
from app.shared.db import Base, make_engine, session_factory

from .conftest import CONFIG

NOA_SAVE = call("save_profile", occupation="מנהלת מוצר", base_salary=18500, commissions=0, bonus=0,
                pension_percent=6, study_fund_percent=2.5, credit_points=2.75, children_under_23=2)


def file_db(tmp_path):
    """SQLite בקובץ (נעילות אמיתיות) עם המתנה קצרה לנעילה, כדי שנעילה תיכשל מהר."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'turns.db').as_posix()}", connect_args={"timeout": 0.5})
    Base.metadata.create_all(engine)
    return session_factory(engine)


class WritingWhilePhrasing(FakeLLM):
    """בזמן קריאת הניסוח (אחרי החישוב) כותב למסד מסשן אחר — כמו חלון שני / נציג."""

    def __init__(self, write):
        super().__init__()
        self.write = write
        self.writes = 0

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        if not tools:
            self.write()          # נכשל ב-"database is locked" אם התור מחזיק נעילת כתיבה
            self.writes += 1
        return super().complete(messages, tools, require_tool, timeout_s)


def test_no_write_lock_is_held_during_llm_calls(tmp_path):
    sessions = file_db(tmp_path)
    holder = {}
    fake = WritingWhilePhrasing(lambda: holder["svc"].start())
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake]))
    holder["svc"] = svc
    turn, token = svc.start()

    fake.turn(NOA_SAVE)
    svc.send(turn.conversation_id, token, "פרופיל")
    fake.turn(call("match_current_car", model="COROLLA CROSS"), call("save_profile", current_copay=300))
    svc.send(turn.conversation_id, token, "קורולה קרוס, 300")
    fake.turn(call("calculate", options=["ATTO 3"]), reply="הנה ההשוואה שביקשת.")
    turn = svc.send(turn.conversation_id, token, "אטו 3")

    assert fake.writes == 1                               # הכתיבה המקבילה הצליחה
    assert turn.stage == Stage.PRESENTED and turn.results[0].result["delta_display"] == "661"
    with sessions() as s:
        run = s.scalars(select(CalculationRunRow)).one()  # נכתבה בשלב (ג) עם המזהה שהוחזר
        assert str(run.id) == turn.results[0].run_id


def test_snapshot_row_exists_from_startup(sessions):
    ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(ConfigSnapshotRow)) == 1


def test_lost_insert_race_reuses_the_winners_row(sessions, monkeypatch):
    """תהליך אחר יצר את השורה בין הבדיקה להוספה: IntegrityError → קוראים את השורה הקיימת."""
    first = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    winner = first.snapshot_id(first.rules.current)

    real_find = ConversationRepository.find_snapshot
    calls = {"n": 0}

    def stale_find(self, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(self, key)   # הבדיקה הראשונה "לא ראתה" אותה

    monkeypatch.setattr(ConversationRepository, "find_snapshot", stale_find)
    second = ConversationService(sessions, RulesStore(CONFIG), LLMChain())
    assert second.snapshot_id(second.rules.current) == winner
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(ConfigSnapshotRow)) == 1


# ---------- שני תורות במקביל על אותה שיחה ----------

class BarrierLLM(FakeLLM):
    """שני התורות נעצרים יחד בחילוץ (שניהם בשלב ב), ואז כל אחד שומר את העיסוק מההודעה שלו."""

    def __init__(self, barrier):
        super().__init__()
        self.barrier = barrier

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        from app.modules.conversation.llm.port import LLMResponse
        if tools and require_tool:
            self.barrier.wait(timeout=10)
            occupation = [m for m in messages if m.role == "user"][-1].content
            return LLMResponse(tool_calls=(call("save_profile", occupation=occupation),))
        return LLMResponse()


def test_concurrent_turns_one_wins_one_conflicts(tmp_path):
    import threading

    from app.modules.conversation.models import ConversationRow, MessageRow
    from app.modules.conversation.public import ConversationConflict, TurnResult

    sessions = file_db(tmp_path)
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain([BarrierLLM(threading.Barrier(2))]))
    turn, token = svc.start()
    outcomes: dict[str, object] = {}

    def send(text):
        try:
            outcomes[text] = svc.send(turn.conversation_id, token, text)
        except Exception as exc:   # noqa: BLE001 — נבדק למטה
            outcomes[text] = exc

    threads = [threading.Thread(target=send, args=(t,)) for t in ("מנהלת מוצר", "מפתח תוכנה")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    winners = [k for k, v in outcomes.items() if isinstance(v, TurnResult)]
    losers = [k for k, v in outcomes.items() if isinstance(v, ConversationConflict)]
    assert len(winners) == 1 and len(losers) == 1, outcomes

    with sessions() as s:
        row = s.scalars(select(ConversationRow)).one()
        contents = [m.content for m in s.scalars(select(MessageRow).order_by(MessageRow.seq))]
    assert (row.version, row.message_count) == (1, 1)
    assert len(contents) == 3 and contents[1] == winners[0] and losers[0] not in contents
    expected = {"מנהלת מוצר": "מנהל/ת מוצר", "מפתח תוכנה": "מפתח/ת תוכנה"}[winners[0]]
    assert row.state["profile"]["occupation"] == expected

    # השיחה ממשיכה רגיל אחרי ההתנגשות
    svc.llm = LLMChain()
    svc.agent.llm = svc.llm
    assert svc.send(turn.conversation_id, token, "18,500").stage == Stage.COLLECT_PROFILE


def test_conflict_is_http_409(engine, monkeypatch):
    from app.modules.conversation.public import ConversationConflict, ConversationService as Service

    from .conftest import make_client

    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def conflict(self, *args, **kwargs):
        raise ConversationConflict()

    monkeypatch.setattr(Service, "send", conflict)
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "שלום"})
    assert r.status_code == 409 and r.json()["detail"] == "ההודעה הקודמת עדיין בטיפול"
