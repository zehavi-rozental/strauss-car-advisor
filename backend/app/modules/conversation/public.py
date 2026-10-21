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
from app.modules.rules.public import RulesSnapshot, RulesStore, catalog_names, find_catalog_car, load_snapshot
from app.modules.vehicles.public import (
    CurrentCarMatch,
    VehicleInfo,
    VehicleKey,
    VehicleRepository,
    match_current_car,
)
from app.shared.db import new_id

from app.modules.cases.public import CasesService, Mode, QuestionAnswered, Status as QuestionStatus

from .expects import ChoiceInvalid, build_expects, tax_label, validate_choice, validate_fields, vehicle_id
from .agent import Agent
from .llm.factory import build_llm_chain
from .llm.port import ChatMessage, LLMChain, LLMError, LLMPort
from .fallback import recorded_text
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
    path: str = ""            # parser / llm / fallback / templates / waiting / choice (ללוג; לא נשלח ב-API)
    say: str = ""             # SAY: llm, or why the template was used (ללוג; לא נשלח ב-API)
    expects: dict[str, Any] = field(default_factory=dict)   # מה השאלה הבאה מצפה לקבל (כפתורים, סוג)


@dataclass
class ConversationView:
    conversation_id: str
    stage: Stage
    state: ConversationState
    messages: list[dict[str, Any]]
    expects: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _PendingRun:
    """הרצת חישוב שנאספה בזיכרון בשלב (ב), ונכתבת רק בשלב (ג)."""

    id: uuid.UUID
    label: str
    inputs: dict[str, Any]
    result: ScenarioResult


def _collector(pending: list) -> Any:
    """record_run של שלב (ב): ההרצה נאספת בזיכרון עם מזהה, ונכתבת רק בשלב (ג)."""
    def record_run(label: str, inputs: dict[str, Any], result: ScenarioResult) -> str:
        run_id = new_id()
        pending.append(_PendingRun(run_id, label, inputs, result))
        return str(run_id)
    return record_run


def _store_runs(repo: ConversationRepository, conv_id: uuid.UUID, snapshot_id: uuid.UUID, pending: list) -> None:
    for run in pending:
        repo.add_run(conv_id, snapshot_id, run.label, run.result.engine_version, run.result.status.value,
                     run.inputs, run.result.model_dump(mode="json"), run.result.delta_display, run_id=run.id)


def _message_json(m: Any) -> dict[str, Any]:
    return {"seq": m.seq, "role": m.role, "source": m.source, "content": m.content,
            "created_at": m.created_at.isoformat()}


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value).strip())
    except ValueError as exc:
        raise ConversationNotFound() from exc


class ConversationService:
    def __init__(self, sessions: sessionmaker, rules: RulesStore, llm: LLMChain,
                 cases: Optional[CasesService] = None, max_messages: int = 80, llm_mode: str = "llm_first"):
        self.sessions = sessions
        self.rules = rules
        self.llm = llm
        self.agent = Agent(llm, llm_mode)
        self.cases = cases or CasesService(sessions)
        self.cases.subscribe(self.apply_answer)   # תשובת נציג → מיושמת על השיחה
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

    def _get_vehicle(self, key: VehicleKey) -> Optional[VehicleInfo]:
        with self.sessions() as session:
            return VehicleRepository(session).get(key)

    SEARCH_LIMIT = 10

    def search_cars(self, query: str) -> list[dict[str, Any]]:
        """חיפוש רכב לבחירה מובנית: הקטלוג קודם, אחר כך מאגר רשות המסים. עד 10 תוצאות."""
        query = query.strip()
        if len(query) < 2:
            return []
        snapshot = self.rules.current
        key = query.lower()
        cars = find_catalog_car(snapshot, query)               # התאמה מדויקת קודם, אחר כך כל שם שמכיל את החיפוש
        cars += [c for c in snapshot.catalog if c not in cars
                 and any(key in n.lower() for n in (c.catalog_code, c.label, *catalog_names(c)))]
        out = [{"id": c.catalog_code, "label": c.label, "source": "catalog", "group": c.group,
                "drive": c.drive.value} for c in cars]
        if len(out) < self.SEARCH_LIMIT:
            with self.sessions() as session:
                found = VehicleRepository(session).search(query, limit=self.SEARCH_LIMIT)
            out += [{"id": vehicle_id(v.key), "label": tax_label(v), "source": "tax", "group": None,
                     "drive": v.drive} for v in found]
        return out[:self.SEARCH_LIMIT]

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
                          source="template", expects=build_expects(state, self.rules.current)), token

    # ---------- תור ----------

    def send(self, conversation_id: str, token: str, text: str) -> TurnResult:
        return self._timed(conversation_id, token, text, None)

    def send_choice(self, conversation_id: str, token: str, ids: list[str],
                    field: Optional[str] = None) -> TurnResult:
        """בחירה מובנית (כפתור): נבדקת מול expects (ChoiceInvalid) ומיושמת בקוד, בלי מודל השפה.
        בשאלה מקובצת field אומר לאיזה שדה הבחירה (חובה רק כשכמה שדות מציעים את אותו מזהה)."""
        return self._timed(conversation_id, token, "", list(ids), field)

    def send_fields(self, conversation_id: str, token: str, values: dict[str, Any]) -> TurnResult:
        """טופס מובנה של שדות פרופיל (Q2 או תיקון): נבדק מול הטווחים (ChoiceInvalid) ונשמר בקוד, בלי מודל
        השפה. ההודעה שנשמרת היא טקסט קריא של הערכים, לתצוגה בלבד."""
        return self._timed(conversation_id, token, "", None, None, values)

    def _timed(self, conversation_id: str, token: str, text: str, choice: Optional[list[str]],
               field: Optional[str] = None, fields: Optional[dict[str, Any]] = None) -> TurnResult:
        started = time.monotonic()
        path, say = "error", ""
        try:
            self._apply_pending(self._authorized(conversation_id, token))
            result = self._send(conversation_id, token, text, choice, field, fields)
            path, say = result.path, result.say
            return result
        finally:
            log.info("turn path=%s say=%s total=%.0fms", path, say, (time.monotonic() - started) * 1000)

    def _send(self, conversation_id: str, token: str, text: str, choice: Optional[list[str]] = None,
              field: Optional[str] = None, fields: Optional[dict[str, Any]] = None) -> TurnResult:
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
        if choice is not None:
            # הבחירה נבדקת מול מה שהוצע במצב שנקרא; ההודעה שנשמרת — התווית בעברית
            text, field = validate_choice(build_expects(state, snapshot), choice, snapshot, self._get_vehicle, field)

        if fields is not None:
            fields = validate_fields(snapshot, fields)      # לפני כל שינוי במצב
            text = recorded_text(fields)

        # (ב) הסוכן — הרצות החישוב נאספות בזיכרון
        pending: list[_PendingRun] = []
        if fields is not None:
            outcome = self.agent.run_fields(snapshot, self._find_vehicle, self._get_vehicle, state, fields, text,
                                            _collector(pending))
        elif choice is not None:
            outcome = self.agent.run_choice(snapshot, self._find_vehicle, self._get_vehicle, state, choice, text,
                                            _collector(pending), field)
        else:
            outcome = self.agent.run_turn(snapshot, self._find_vehicle, state, history, text, _collector(pending))

        # (ג) כתיבה — רק אם הגרסה לא השתנתה מאז (א); אחרת התור כולו מבוטל
        with self.sessions() as session, session.begin():
            repo = ConversationRepository(session)
            saved = repo.save_turn(conv_id, loaded_version, state=state.model_dump(mode="json"),
                                   stage=state.stage.value, message_count=loaded_count + 1, llm_mode=self.llm.mode)
            if not saved:
                log.warning("turn discarded: conversation changed by a concurrent turn")
                raise ConversationConflict()
            self._store_questions(session, repo, conv_id, state, outcome)
            # seq מחושב כאן, אחרי העדכון ותחת אותה נעילת כתיבה
            repo.add_message(conv_id, "user", text, "user")
            repo.add_message(conv_id, "assistant", outcome.reply, outcome.source, outcome.guard_blocked)
            _store_runs(repo, conv_id, snapshot_id, pending)

        return TurnResult(conversation_id=str(conv_id), stage=state.stage, reply=outcome.reply,
                          source=outcome.source,
                          results=state.results if outcome.just_calculated else [],
                          pending_rep=state.pending_rep.reason if state.pending_rep else None,
                          path=outcome.path, say=outcome.say, expects=build_expects(state, snapshot))

    def _store_questions(self, session, repo: ConversationRepository, conv_id: uuid.UUID,
                         state: ConversationState, outcome) -> None:
        """שאלות לנציג — באותה טרנזקציה: תור שבוטל (409) לא משאיר שאלה, ותור חוזר לא יוצר כפיל."""
        self.cases.cancel(session, outcome.cancel_question_ids)
        opened = [self.cases.open_question(session, str(conv_id), req) for req in outcome.question_requests]
        required = [q.id for q in opened if q.mode == Mode.REQUIRED]
        if required and state.pending_rep is not None:
            state.pending_rep.question_ids = list(dict.fromkeys(state.pending_rep.question_ids + required))
            repo.update_state(conv_id, state.model_dump(mode="json"))

    # ---------- תשובות הנציג ----------

    APPLY_RETRIES = 3

    def apply_answer(self, event: QuestionAnswered) -> bool:
        """מיישם תשובת נציג באותה בדיקת גרסה כמו תור (ניסיון חוזר בהתנגשות). אידמפוטנטי: תשובה
        שכבר יושמה לא משנה דבר. מחזיר True אם נכתב משהו."""
        conv_id = uuid.UUID(event.conversation_id)
        for _ in range(self.APPLY_RETRIES):
            snapshot = self.rules.current
            snapshot_id = self.snapshot_id(snapshot)
            with self.sessions() as session:                                 # (א)
                row = ConversationRepository(session).get(conv_id)
                if row is None:
                    return False
                state = ConversationState.model_validate(row.state)
                if event.question_id in state.applied_answers:
                    return False
                loaded_count, loaded_version = row.message_count, row.version
            pending: list[_PendingRun] = []                                  # (ב)
            outcome = self.agent.apply_answer(snapshot, self._find_vehicle, state, event, _collector(pending))
            with self.sessions() as session, session.begin():                # (ג)
                repo = ConversationRepository(session)
                if not repo.save_turn(conv_id, loaded_version, state=state.model_dump(mode="json"),
                                      stage=state.stage.value, message_count=loaded_count, llm_mode=self.llm.mode):
                    continue                                                 # תור מקביל כתב — שוב מההתחלה
                self._store_questions(session, repo, conv_id, state, outcome)
                if outcome.reply:
                    repo.add_message(conv_id, "assistant", outcome.reply, "rep")
                _store_runs(repo, conv_id, snapshot_id, pending)
            log.info("rep answer %s applied to the conversation", event.question_id)
            return True
        log.warning("rep answer %s not applied after %d conflicts; it will be applied on the next read",
                    event.question_id, self.APPLY_RETRIES)
        return False

    def _apply_pending(self, conv_id: uuid.UUID) -> None:
        """תשובות שנשמרו אבל לא יושמו (למשל המנוי נכשל) — מיושמות בקריאה הבאה של השיחה."""
        with self.sessions() as session:
            row = ConversationRepository(session).get(conv_id)
            applied = set(ConversationState.model_validate(row.state).applied_answers) if row else set()
        for q in self.cases.list_for_conversation(str(conv_id), QuestionStatus.ANSWERED):
            if q.id not in applied and q.answer_value is not None:
                self.apply_answer(QuestionAnswered(
                    conversation_id=q.conversation_id, question_id=q.id, missing_field=q.missing_field,
                    option_id=q.option_id, value=q.answer_value, effective_date=q.effective_date,
                    note=q.answer_note))

    def _authorized(self, conversation_id: str, token: str) -> uuid.UUID:
        with self.sessions() as session:
            return self._load(ConversationRepository(session), conversation_id, token).id

    def updates(self, conversation_id: str, token: str, after: int = 0) -> dict[str, Any]:
        """לתשאול (polling): הודעות חדשות אחרי seq, שלב, שאלות פתוחות ותוצאות אחרונות."""
        conv_id = self._authorized(conversation_id, token)
        self._apply_pending(conv_id)
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = repo.get(conv_id)
            state = ConversationState.model_validate(row.state)
            messages = [_message_json(m) for m in repo.messages(conv_id) if m.seq > after]
        pending = [{"missing_field": q.missing_field.value, "mode": q.mode.value, "status": q.status.value,
                    "option_id": q.option_id or None}
                   for q in self.cases.list_for_conversation(str(conv_id), QuestionStatus.OPEN)]
        return {"conversation_id": str(conv_id), "stage": state.stage.value, "messages": messages,
                "pending_questions": pending, "expects": build_expects(state, self.rules.current),
                "results": [r.model_dump(mode="json") for r in state.results]}

    def view(self, conversation_id: str, token: str) -> ConversationView:
        self._apply_pending(self._authorized(conversation_id, token))
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            state = ConversationState.model_validate(row.state)
            messages = [_message_json(m) for m in repo.messages(row.id)]
            return ConversationView(conversation_id=str(row.id), stage=state.stage, state=state,
                                    messages=messages, expects=build_expects(state, self.rules.current))

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
    "ConversationView", "LLMChain", "LLMPort", "OptionResult", "RepReason", "Stage",
    "TurnResult", "build_llm_chain", "reproduce_run", "ChoiceInvalid",
]
