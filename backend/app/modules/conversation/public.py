"""הממשק הציבורי של מודול השיחה: ConversationService ומה שה-API צריך."""
from __future__ import annotations

import hmac
import logging
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.modules.calculation.public import (
    Employee,
    OccupationRule,
    ScenarioResult,
    State,
    evaluate,
)
from app.modules.rules.public import RulesSnapshot, RulesStore, load_snapshot
from app.modules.vehicles.public import CurrentCarMatch, VehicleRepository, match_current_car
from app.shared.db import new_id

from .agent import Agent, NullRepresentative, RepresentativePort
from .llm.factory import build_llm_chain
from .llm.port import ChatMessage, LLMChain, LLMError, LLMPort
from .parsing import redact_id_numbers
from .repository import (
    DATA_FILE,
    EXTENSIONS_FILE,
    RULES_FILE,
    ConversationRepository,
    combined_hash,
    token_hash,
)
from .stage import ConversationState, OptionResult, RepReason, Stage


log = logging.getLogger(__name__)


class ConversationNotFound(Exception):
    """לא קיימת, או שהעוגייה לא שלה (לא מבדילים — כדי לא לחשוף קיום)."""


class ConversationLimitReached(Exception):
    pass


class ConversationConflict(Exception):
    """תור אחר על אותה שיחה נכתב בזמן שהתור הזה רץ. התור הזה בוטל ולא נשמר דבר ממנו."""


@dataclass
class TurnResult:
    conversation_id: str
    stage: Stage
    reply: str
    source: str
    results: list[OptionResult] = field(default_factory=list)
    pending_rep: Optional[RepReason] = None
    path: str = ""            # parser / llm / fallback / templates / waiting (ללוג; לא נשלח ב-API)


@dataclass
class ConversationView:
    conversation_id: str
    stage: Stage
    state: ConversationState
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class _PendingRun:
    """הרצת חישוב שנאספה בזיכרון בשלב (ב), ונכתבת רק בשלב (ג)."""

    id: uuid.UUID
    label: str
    inputs: dict[str, Any]
    result: ScenarioResult


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ConversationNotFound() from exc


class ConversationService:
    def __init__(self, sessions: sessionmaker, rules: RulesStore, llm: LLMChain,
                 rep: Optional[RepresentativePort] = None, max_messages: int = 80):
        self.sessions = sessions
        self.rules = rules
        self.llm = llm
        self.agent = Agent(llm, rep or NullRepresentative())
        self.max_messages = max_messages
        self._snapshot_ids: dict[str, uuid.UUID] = {}
        self._snapshot_lock = threading.Lock()
        self.snapshot_id(rules.current)        # בהפעלה: שורת הגרסה קיימת לפני התור הראשון

    # ---------- גרסת הגדרות ----------

    def snapshot_id(self, snapshot: RulesSnapshot) -> uuid.UUID:
        """המזהה של שורת הגרסה. נוצרת פעם אחת (בהפעלה, ושוב אחרי טעינה מחדש של הכללים),
        בטרנזקציה קצרה משלה — לא בתוך כתיבת התור — כך ששני תורות ראשונים לא מתחרים על המפתח."""
        key = combined_hash(snapshot)
        cached = self._snapshot_ids.get(key)
        if cached is not None:
            return cached
        with self._snapshot_lock:
            if key not in self._snapshot_ids:
                self._snapshot_ids[key] = self._ensure_snapshot(snapshot, key)
            return self._snapshot_ids[key]

    def _ensure_snapshot(self, snapshot: RulesSnapshot, key: str) -> uuid.UUID:
        with self.sessions() as session:
            found = ConversationRepository(session).find_snapshot(key)
        if found is not None:
            return found
        try:
            with self.sessions() as session, session.begin():
                return ConversationRepository(session).insert_snapshot(snapshot)
        except IntegrityError:   # תהליך אחר יצר אותה בינתיים
            with self.sessions() as session:
                found = ConversationRepository(session).find_snapshot(key)
            assert found is not None
            return found

    def _find_vehicle(self, model: str, reg_year: Optional[int]) -> CurrentCarMatch:
        """חיפוש במאגר בסשן קריאה קצר משלו (שלב ב רץ בלי סשן פתוח)."""
        with self.sessions() as session:
            return match_current_car(VehicleRepository(session), model, reg_year)

    # ---------- שיחה חדשה ----------

    def start(self) -> tuple[TurnResult, str]:
        """מחזיר את תור הפתיחה ואת טוקן השיחה (נשמר רק כ-hash)."""
        token = secrets.token_urlsafe(32)
        state = ConversationState()
        greeting = self.agent.greeting(state, self.rules.current)
        with self.sessions() as session, session.begin():
            repo = ConversationRepository(session)
            row = repo.create(state.model_dump(mode="json"), state.stage.value, token_hash(token), self.llm.mode)
            repo.add_message(row.id, "assistant", greeting, "template")
            conversation_id = str(row.id)
        return TurnResult(conversation_id=conversation_id, stage=state.stage, reply=greeting,
                          source="template"), token

    # ---------- תור ----------

    def send(self, conversation_id: str, token: str, text: str) -> TurnResult:
        started = time.monotonic()
        path = "error"
        try:
            result = self._send(conversation_id, token, text)
            path = result.path
            return result
        finally:
            log.info("turn path=%s total=%.0fms", path, (time.monotonic() - started) * 1000)

    def _send(self, conversation_id: str, token: str, text: str) -> TurnResult:
        """שלושה שלבים, כדי שאף טרנזקציה לא תהיה פתוחה בזמן קריאות למודל השפה:
        (א) קריאה קצרה, (ב) הסוכן בלי סשן פתוח, (ג) כתיבה קצרה של כל התור."""
        snapshot = self.rules.current
        snapshot_id = self.snapshot_id(snapshot)   # אחרי טעינה מחדש — נוצרת כאן, מחוץ לתור
        text = redact_id_numbers(text)

        # (א) קריאה
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            if row.message_count >= self.max_messages:
                raise ConversationLimitReached()
            conv_id, loaded_count, loaded_version = row.id, row.message_count, row.version
            state = ConversationState.model_validate(row.state)
            history = [ChatMessage("user" if m.role == "user" else "assistant", m.content)
                       for m in repo.messages(row.id)]

        # (ב) הסוכן — הרצות החישוב נאספות בזיכרון
        pending: list[_PendingRun] = []

        def record_run(label: str, inputs: dict[str, Any], result: ScenarioResult) -> str:
            run_id = new_id()
            pending.append(_PendingRun(run_id, label, inputs, result))
            return str(run_id)

        outcome = self.agent.run_turn(snapshot, self._find_vehicle, state, history, text, record_run)

        # (ג) כתיבה — רק אם הגרסה לא השתנתה מאז (א); אחרת התור כולו מבוטל
        with self.sessions() as session, session.begin():
            repo = ConversationRepository(session)
            saved = repo.save_turn(conv_id, loaded_version, state=state.model_dump(mode="json"),
                                   stage=state.stage.value, message_count=loaded_count + 1, llm_mode=self.llm.mode)
            if not saved:
                log.warning("turn discarded: conversation changed by a concurrent turn")
                raise ConversationConflict()
            # seq מחושב כאן, אחרי העדכון ותחת אותה נעילת כתיבה
            repo.add_message(conv_id, "user", text, "user")
            repo.add_message(conv_id, "assistant", outcome.reply, outcome.source, outcome.guard_blocked)
            for run in pending:
                repo.add_run(conv_id, snapshot_id, run.label, run.result.engine_version, run.result.status.value,
                             run.inputs, run.result.model_dump(mode="json"), run.result.delta_display, run_id=run.id)

        return TurnResult(conversation_id=str(conv_id), stage=state.stage, reply=outcome.reply,
                          source=outcome.source,
                          results=state.results if outcome.just_calculated else [],
                          pending_rep=state.pending_rep.reason if state.pending_rep else None,
                          path=outcome.path)

    def view(self, conversation_id: str, token: str) -> ConversationView:
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            state = ConversationState.model_validate(row.state)
            messages = [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                        for m in repo.messages(row.id)]
            return ConversationView(conversation_id=str(row.id), stage=state.stage, state=state,
                                    messages=messages)

    def _load(self, repo: ConversationRepository, conversation_id: str, token: str):
        row = repo.get(_uuid(conversation_id))
        if row is None or not token or not hmac.compare_digest(row.session_token_hash, token_hash(token)):
            raise ConversationNotFound()
        return row


# ---------- שחזור חישוב ----------

_STATE = TypeAdapter(State)


def reproduce_run(sessions: sessionmaker, run_id: str) -> ScenarioResult:
    """מריץ שוב חישוב שמור מול קבצי ההגדרות ששמורים איתו — לא מול מה שבתיקייה היום."""
    with sessions() as session:
        repo = ConversationRepository(session)
        run = repo.get_run(_uuid(run_id))
        if run is None:
            raise ConversationNotFound()
        snap_row = repo.get_snapshot(run.config_snapshot_id)
        assert snap_row is not None
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # bytes ולא write_text: בלי המרת שורות, הקובץ זהה למקור
            (d / RULES_FILE).write_bytes(snap_row.strauss_rules_json.encode("utf-8"))
            (d / EXTENSIONS_FILE).write_bytes(snap_row.extensions_yaml.encode("utf-8"))
            (d / DATA_FILE).write_bytes(snap_row.strauss_data_xlsx)
            snapshot = load_snapshot(d)
        inputs = run.inputs
        occupation = OccupationRule.model_validate(inputs["occupation"]) if inputs["occupation"] else None
        return evaluate(Employee.model_validate(inputs["employee"]), _STATE.validate_python(inputs["current"]),
                        _STATE.validate_python(inputs["alternative"]), snapshot.calc, occupation)


__all__ = [
    "ChatMessage", "LLMError",
    "ConversationConflict", "ConversationLimitReached", "ConversationNotFound", "ConversationService",
    "ConversationState",
    "ConversationView", "LLMChain", "LLMPort", "OptionResult", "RepReason", "RepresentativePort", "Stage",
    "TurnResult", "build_llm_chain", "reproduce_run",
]
