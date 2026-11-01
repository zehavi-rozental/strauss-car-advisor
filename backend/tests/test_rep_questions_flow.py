"""פתיחת שאלות לנציג מהשיחה: באותה טרנזקציה של התור, בלי כפילויות, ולפי מדיניות ההשתתפות."""
from __future__ import annotations

import re

import shutil
import threading

from app.modules.cases.public import MissingField, Mode, Status
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.llm.port import LLMResponse
from app.modules.conversation.public import ConversationConflict, ConversationService, LLMChain, Stage, TurnResult
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, CONFIG_ASYNC, make_client
from .legacy_wait import seed_legacy_wait
from .test_turn_transactions import file_db

NOA = [
    "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
    "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש",
    "אטו 3",
]
# C1 (22/10): the amount used and its source are said, and that the representative was asked to approve it
PENDING = ("חישבתי עם השתתפות של 300 ₪ בחודש ב-ATTO 3 (2025), לפי הסכום שבקטלוג שטראוס.\n"
           "הסכום עוד לא אושר עבורכם: שלחתי לנציג התחבורה בקשה לאשר אותו, וכשיענה אעדכן כאן את החישוב.")
# a stated group the occupations table contradicts: the one blocking wait left (C4)
CONFLICT = ["קבוצה ב, שכר בסיס 18,500", "אני מנהלת מוצר",
            "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים",
            "יש לי קורולה קרוס, משלמת 300"]


def service(sessions, llm=None, config=CONFIG_ASYNC):
    return ConversationService(sessions, RulesStore(config), llm or LLMChain())


def talk(svc, messages, start=None):
    turn, token = start or svc.start()
    for m in messages:
        turn = svc.send(turn.conversation_id, token, m)
    return turn, token


# ---------- השתתפות ברכב חדש: CONFIRM, התוצאה מוצגת מיד ----------

def test_new_car_copay_question_is_opened_once_and_the_result_is_shown(engine):
    client = make_client(engine, config_dir=CONFIG_ASYNC)
    cases = client.app.state.ctx.service.cases
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in NOA:
        body = client.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
    assert body["stage"] == "PRESENTED" and "יגדל ב-661 ₪" in body["reply"] and PENDING in body["reply"]
    [q] = cases.list_open()
    assert (q.missing_field, q.mode, q.option_id, q.suggested_value) == (
        MissingField.NEW_CAR_COPAY, Mode.CONFIRM, "CAR-10", "300")
    assert q.context == {"group": "ג", "car_label": "ATTO 3 (2025)", "catalog_code": "CAR-10", "suggested_amount": "300"}
    assert q.question_text == "מה ההשתתפות החודשית של העובד ב-ATTO 3 (2025)? לפי הקטלוג: 300 ₪"

    client.post(f"/api/conversations/{cid}/messages", json={"text": "אטו 3"})   # אותה שאלה שוב
    assert [x.id for x in cases.list_open()] == [q.id]


# ---------- המתנה לנציג ----------

def test_waiting_makes_no_llm_call(sessions):
    fake = FakeLLM()
    svc = service(sessions, LLMChain([fake]))
    turn, token = talk(svc, CONFLICT)
    assert turn.stage == Stage.WAITING_FOR_REP
    before = len(fake.calls)
    turn = svc.send(turn.conversation_id, token, "נו, יש תשובה?")
    assert turn.path == "waiting" and len(fake.calls) == before
    # the question is answered from the FAQ, then the waiting line (unchanged)
    assert turn.reply.startswith("התשובה של נציג התחבורה תופיע כאן")
    assert turn.reply.endswith("שאלתי את נציג התחבורה ואעדכן כשתגיע תשובה.")


def test_correcting_the_car_cancels_its_questions(engine):
    client = make_client(engine, config_dir=CONFIG_ASYNC)
    cases = client.app.state.ctx.service.cases
    cid = client.post("/api/conversations").json()["conversation_id"]
    for text in [NOA[0], "יש לי קורולה 2026", "לא יודע"]:
        client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    assert cases.list_open() == []                                   # rule A: today's copay is the employee's
    seed_legacy_wait(client.app.state.ctx.service, cid)              # a question opened before rule A (22/10)
    [old] = cases.list_open()
    assert old.missing_field == MissingField.CURRENT_COPAY and old.context["catalog_code"] == "CAR-07"

    body = client.post(f"/api/conversations/{cid}/messages", json={"text": "לא, זו קורולה קרוס"}).json()
    assert cases.get(old.id).status == Status.CANCELLED and cases.list_open() == []
    assert body["stage"] == "COLLECT_CURRENT_CAR"
    assert body["reply"].startswith("זיהיתי: COROLLA CROSS (2025)") and "ההשתתפות החודשית ב-" in body["reply"]
    state = client.get(f"/api/conversations/{cid}").json()
    assert state["pending_rep"] is None and state["current_car"]["catalog_code"] == "CAR-09"


# ---------- מדיניות ההשתתפות מ-extensions.yaml ----------

def _policy(tmp_path, policy):
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    p = config / "extensions.yaml"
    text = p.read_text(encoding="utf-8")
    assert re.search(r"(?m)^copay_policy:", text)
    p.write_text(re.sub(r"(?m)^copay_policy:.*$", f"copay_policy: {policy}", text), encoding="utf-8")
    return config


def test_catalog_default_policy_opens_no_question(sessions, tmp_path):
    svc = service(sessions, config=_policy(tmp_path, "CATALOG_DEFAULT"))
    turn, _ = talk(svc, NOA)
    assert turn.stage == Stage.PRESENTED and "יגדל ב-661 ₪" in turn.reply and PENDING not in turn.reply
    assert svc.cases.list_open() == []


def test_rep_required_policy_waits_and_hides_the_result(sessions, tmp_path):
    svc = service(sessions, config=_policy(tmp_path, "REP_REQUIRED"))
    turn, token = talk(svc, NOA)
    assert turn.stage == Stage.WAITING_FOR_REP and "661" not in turn.reply
    # alone (no result above it, no amount used): the scenario's waiting sentence, with "תודה!"
    assert ("תודה! חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה."
            in turn.reply)
    [q] = svc.cases.list_open()
    assert (q.missing_field, q.mode) == (MissingField.NEW_CAR_COPAY, Mode.REQUIRED)
    assert svc.view(turn.conversation_id, token).state.pending_rep.question_ids == [q.id]


# ---------- תור שבוטל (409) לא משאיר שאלה ----------

class ChooseAtTheBarrier(FakeLLM):
    """שני תורות בוחרים חלופה שונה ונעצרים יחד לפני הכתיבה; רק אחד נשמר."""

    CHOICE = {"אפשרות אלפא: אטו 3": "ATTO 3", "אפשרות בטא: אי וי 3": "EV3"}

    def __init__(self, barrier):
        super().__init__()
        self.barrier = barrier

    def complete(self, messages, tools=(), require_tool=False, timeout_s=None):
        if tools and messages[-1].role == "user":          # round 1 (round 2 sees the tool results)
            self.barrier.wait(timeout=10)
            text = messages[-1].content.split("\n")[1]          # inside <<<USER>>> … <<<END>>>
            return LLMResponse(tool_calls=(call("calculate", options=[self.CHOICE[text]]),))
        return LLMResponse()


def test_a_discarded_turn_leaves_no_question(tmp_path):
    sessions = file_db(tmp_path)
    svc = ConversationService(sessions, RulesStore(CONFIG_ASYNC), LLMChain([ChooseAtTheBarrier(threading.Barrier(2))]),
                              llm_mode="parser_first")       # ההכנה בקורא; רק שתי ההודעות המתחרות במודל
    turn, token = talk(svc, NOA[:2])                      # הקורא — בלי מודל
    assert turn.stage == Stage.CHOOSE_OPTIONS
    outcomes = {}

    def send(text):
        try:
            outcomes[text] = svc.send(turn.conversation_id, token, text)
        except Exception as exc:  # noqa: BLE001
            outcomes[text] = exc

    threads = [threading.Thread(target=send, args=(t,)) for t in ChooseAtTheBarrier.CHOICE]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    [winner] = [k for k, v in outcomes.items() if isinstance(v, TurnResult)]
    assert sum(isinstance(v, ConversationConflict) for v in outcomes.values()) == 1
    [q] = svc.cases.list_open()
    assert q.option_id == {"ATTO 3": "CAR-10", "EV3": "CAR-11"}[ChooseAtTheBarrier.CHOICE[winner]]
