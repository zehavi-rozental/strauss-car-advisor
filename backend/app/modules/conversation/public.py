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

from app.modules.cases.public import CasesService, MissingField, Mode, QuestionAnswered, Status as QuestionStatus

from .expects import ChoiceInvalid, FieldsInvalid, build_expects, tax_label, validate_choice, validate_fields, vehicle_id
from .email_notifications import EmailNotificationService, EmailNotConfigured, InvalidEmailLink
from .agent import Agent, RepQuestionResult, general_question_id
from .models import ConversationRow
from .understanding import rep_scope_ok_in_context
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
from .option_map import map_json
from .stage import ConversationState, OptionResult, RepReason, Stage, map_missing, map_ready, results_stale
from .templates import FIELD_NAMES


log = logging.getLogger(__name__)


def map_readiness(state: ConversationState) -> dict[str, Any]:
    """For the view and the updates (additive, 22/10): whether the option map can be built now, and what it
    still needs, in Hebrew. The "גלו לי את כל האפשרויות" button is offered only when ready."""
    return {"map_ready": map_ready(state),
            "map_missing": [FIELD_NAMES[f] for f in map_missing(state) if f in FIELD_NAMES]}


class ConversationNotFound(Exception):
    """לא קיימת, או שהעוגייה לא שלה (לא מבדילים — כדי לא לחשוף קיום)."""


class ConversationLimitReached(Exception):
    pass


class ConversationConflict(Exception):
    """תור אחר על אותה שיחה נכתב בזמן שהתור הזה רץ. התור הזה בוטל ולא נשמר דבר ממנו."""


class RepQuestionInvalid(ValueError):
    """שאלה לנציג שאי אפשר לשלוח: ריקה, ארוכה מדי, או מחוץ להיקף (rep_scope_ok). ההודעה בעברית, לתצוגה לעובד."""


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
    results_stale: bool = False   # the results on record predate the last change (stage.results_stale)


@dataclass
class ConversationView:
    conversation_id: str
    stage: Stage
    state: ConversationState
    messages: list[dict[str, Any]]
    expects: dict[str, Any] = field(default_factory=dict)
    rep_questions: list[dict[str, Any]] = field(default_factory=list)


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
            "created_at": m.created_at.isoformat(), "rep_question_id": getattr(m, "rep_question_id", None)}


def _rep_question_json(q: Any) -> dict[str, Any]:
    return {
        "id": q.id, "missing_field": q.missing_field.value, "question_text": q.question_text,
        "car_label": q.context.get("car_label"), "status": q.status.value,
        "created_at": q.created_at.isoformat(),
        "answered_at": q.answered_at.isoformat() if q.answered_at else None,
        "answer_value": q.answer_value, "answer_note": q.answer_note,
    }


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value).strip())
    except ValueError as exc:
        raise ConversationNotFound() from exc


class ConversationService:
    def __init__(self, sessions: sessionmaker, rules: RulesStore, llm: LLMChain,
                 cases: Optional[CasesService] = None, max_messages: int = 80, llm_mode: str = "llm_first",
                 email_notifications: Optional[EmailNotificationService] = None):
        self.sessions = sessions
        self.rules = rules
        self.llm = llm
        self.agent = Agent(llm, llm_mode)
        self.cases = cases or CasesService(sessions)
        self.email_notifications = email_notifications
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

    def send_fields(self, conversation_id: str, token: str, values: dict[str, Any],
                    source: Optional[str] = None) -> TurnResult:
        """טופס מובנה של שדות פרופיל (Q2 או תיקון): נבדק מול הטווחים (FieldsInvalid, כל השדות השגויים;
        דבר לא נשמר) ונשמר בקוד, בלי מודל השפה. ההודעה שנשמרת היא טקסט קריא של הערכים, לתצוגה בלבד.
        source="payslip": values the employee reviewed and approved from a payslip (said so in the history)."""
        return self._timed(conversation_id, token, "", None, None, values, source)

    def _timed(self, conversation_id: str, token: str, text: str, choice: Optional[list[str]],
               field: Optional[str] = None, fields: Optional[dict[str, Any]] = None,
               source: Optional[str] = None) -> TurnResult:
        started = time.monotonic()
        path, say = "error", ""
        try:
            self._apply_pending(self._authorized(conversation_id, token))
            result = self._send(conversation_id, token, text, choice, field, fields, source)
            path, say = result.path, result.say
            return result
        finally:
            log.info("turn path=%s say=%s total=%.0fms", path, say, (time.monotonic() - started) * 1000)

    def _send(self, conversation_id: str, token: str, text: str, choice: Optional[list[str]] = None,
              field: Optional[str] = None, fields: Optional[dict[str, Any]] = None,
              source: Optional[str] = None) -> TurnResult:
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
            text = ("מהתלוש (אושר): " if source == "payslip" else "") + recorded_text(fields)

        # (ב) הסוכן — הרצות החישוב נאספות בזיכרון
        pending: list[_PendingRun] = []
        if fields is not None:
            outcome = self.agent.run_fields(snapshot, self._find_vehicle, self._get_vehicle, state, fields, text,
                                            _collector(pending), source)
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
                          path=outcome.path, say=outcome.say, expects=build_expects(state, snapshot),
                          results_stale=results_stale(state))

    def _store_questions(self, session, repo: ConversationRepository, conv_id: uuid.UUID,
                         state: ConversationState, outcome) -> None:
        """שאלות לנציג — באותה טרנזקציה: תור שבוטל (409) לא משאיר שאלה, ותור חוזר לא יוצר כפיל."""
        self.cases.cancel(session, outcome.cancel_question_ids)
        if getattr(outcome, "copay_selection", None) is not None:
            self.cases.cancel_open_except(session, str(conv_id), MissingField.NEW_CAR_COPAY, outcome.copay_selection)
        opened = [self.cases.open_question(session, str(conv_id), req) for req in outcome.question_requests]
        required = [q.id for q in opened if q.mode == Mode.REQUIRED]
        if required and state.pending_rep is not None:
            state.pending_rep.question_ids = list(dict.fromkeys(state.pending_rep.question_ids + required))
            repo.update_state(conv_id, state.model_dump(mode="json"))

    # ---------- פנייה יזומה של העובד לנציג (rep-question endpoint, Package A) ----------

    MAX_REP_QUESTION_CHARS = 500

    def rep_question(self, conversation_id: str, token: str, question: str,
                     intent: str = "general", option_id: Optional[str] = None,
                     email: Optional[str] = None) -> dict[str, Any]:
        """שאלה שהעובד יוזם לנציג, דרך מנגנון הפניות הקיים — בלי understand/parser/LLM.

        הסיווג נקבע ע"י הכוונה (intent), לא ע"י בחירת רכב/סכום/מילת מפתח, ומאומת בשרת מול מצב השיחה:
        intent='copay_verification' + רכב תקף שנמצא בתוצאות המורשות עם מקור קטלוגי וסכום → NEW_CAR_COPAY
        (תשובת הנציג עשויה לעדכן את החישוב של אותו רכב). כל מצב אחר, לרבות כל ספק → GENERAL_QUESTION
        (אינפורמטיבי, לעולם לא משנה חישוב). לקוח לא יכול להפוך שאלה כללית לאישור סכום: הכוונה בלבד קובעת,
        והשרת מאמת את הרכב. מניעת כפילות דרך האינדקס הייחודי הקיים (בלי replace): פנייה פתוחה זהה מוחזרת
        כ-exists עם הנוסח השמור, ונושאים שונים לא נחסמים זה את זה."""
        question = " ".join((question or "").split())
        if not question:
            raise RepQuestionInvalid("השאלה ריקה. כתבו מה תרצו לברר מול נציג הרכב.")
        if len(question) > self.MAX_REP_QUESTION_CHARS:
            raise RepQuestionInvalid(f"השאלה ארוכה מדי (עד {self.MAX_REP_QUESTION_CHARS} תווים).")
        question = redact_id_numbers(question)
        snapshot = self.rules.current
        snapshot_id = self.snapshot_id(snapshot)

        for _ in range(self.APPLY_RETRIES):
            # (א) קריאה קצרה — אימות הרשאה וגרסה
            with self.sessions() as session:
                repo = ConversationRepository(session)
                row = self._load(repo, conversation_id, token)
                conv_id, loaded_count, loaded_version = row.id, row.message_count, row.version
                state = ConversationState.model_validate(row.state)

            # סיווג + הגנת היקף. GENERAL_QUESTION עובר את הגנת ההיקף, אבל בהתחשב בהקשר השיחה: כשיש רכב על
            # המסך (רכב נוכחי או חלופות מושוות), שאלה טבעית קצרה בהקשר הרכב מתקבלת ("אפשר עם וו גרירה?"),
            # ורק שאלה מניפולטיבית, מחוץ לנושא או על השכר האישי נדחית. NEW_CAR_COPAY הוא בירור מובנה לרכב ידוע.
            field = MissingField.NEW_CAR_COPAY if intent == "copay_verification" else MissingField.GENERAL_QUESTION
            if field == MissingField.GENERAL_QUESTION:
                has_car_context = state.current.source is not None or bool(state.results)
                if not rep_scope_ok_in_context(snapshot, question, has_car_context=has_car_context):
                    raise RepQuestionInvalid(
                        "אפשר לשלוח לנציג רק שאלה על הרכב, הזכאות או התנאים. נסחו אותה שוב, למשל על רכב מסוים, "
                        "זמינות, אבזור או תנאי השימוש.")

            # (ב) מפתח ה-dedup לפי הסוג, ובדיקת פנייה פתוחה קיימת (בלי replace)
            if field == MissingField.NEW_CAR_COPAY:
                dedup_option = option_id or ""
            else:
                dedup_option = general_question_id(question)
            existing = self._open_question_text(str(conv_id), field, dedup_option)

            # (ג) הסוכן בונה את הפנייה ומסמן את התוצאה (בלי סשן פתוח)
            pending: list[_PendingRun] = []
            outcome = self.agent.rep_question(snapshot, self._find_vehicle, state, question, field, option_id,
                                              existing, _collector(pending))
            result: RepQuestionResult = outcome.rep_question_result
            if result.status in ("exists", "invalid"):
                # שום דבר לא נפתח: מצב השיחה לא השתנה, אין מה לכתוב
                email_state = self.email_status(conversation_id)
                return {**self._rep_question_payload(result, state), "email_verified": email_state["verified"],
                        "email_status": email_state["status"]}

            # (ד) כתיבה קצרה — רק אם הגרסה לא השתנתה; אחרת מנסים שוב מההתחלה
            with self.sessions() as session, session.begin():
                repo = ConversationRepository(session)
                if not repo.save_turn(conv_id, loaded_version, state=state.model_dump(mode="json"),
                                      stage=state.stage.value, message_count=loaded_count, llm_mode=self.llm.mode):
                    continue
                if self.email_notifications is None:
                    raise EmailNotConfigured("Email service is not configured")
                conversation = repo.get(conv_id)
                if conversation is None:
                    raise ConversationNotFound()
                if not email and self.email_notifications.settings.email_backend != "fake":
                    raise ValueError("כתובת אימייל נדרשת בפנייה הראשונה.")
                opened = [self.cases.open_question(session, str(conv_id), req)
                          for req in outcome.question_requests]
                if email and opened:
                    self.email_notifications.prepare_verification(session, conversation, email, opened[0].id)
                if result.status == "created" and opened:
                    repo.add_message(
                        conv_id, "assistant", f"שלחתם פנייה לנציג הרכב:\n{opened[0].question_text}",
                        "rep_inquiry", rep_question_id=opened[0].id)
                _store_runs(repo, conv_id, snapshot_id, pending)
            if opened:
                result.question_id = opened[0].id
            self.email_notifications.process_pending()
            log.info("rep question opened field=%s status=%s", result.missing_field, result.status)
            email_state = self.email_status(conversation_id)
            return {**self._rep_question_payload(result, state), "email_verified": email_state["verified"],
                    "email_status": email_state["status"]}
        raise ConversationConflict()

    def email_status(self, conversation_id: str) -> dict[str, Any]:
        if self.email_notifications is None:
            return {"verified": False, "email": None, "status": "pending"}
        with self.sessions() as session:
            row = session.get(ConversationRow, _uuid(conversation_id))
            if row is None or not row.email_verified_at or not row.email_ciphertext:
                if row is None:
                    return {"verified": False, "email": None, "status": "pending"}
                return {"verified": False, "email": None,
                        "status": self.email_notifications.verification_status(session, row)}
            return {"verified": True, "email": self.email_notifications._decrypt(row.email_ciphertext),
                    "status": "verified"}

    def redeem_email_link(self, token: str) -> tuple[str, str, str | None]:
        if self.email_notifications is None:
            raise InvalidEmailLink()
        conversation_id, session_token, question_id = self.email_notifications.redeem(token)
        self.email_notifications.process_pending()
        return conversation_id, session_token, question_id

    def _open_question_text(self, conversation_id: str, field: "MissingField", option_id: str) -> Optional[str]:
        """הנוסח של פנייה פתוחה קיימת לאותו (שיחה, סוג, רכב/hash), או None. לזיהוי כפילות בלי replace."""
        for q in self.cases.list_for_conversation(conversation_id, QuestionStatus.OPEN):
            if q.missing_field == field and (q.option_id or "") == option_id:
                return q.question_text
        return None

    def _rep_question_payload(self, result: RepQuestionResult, state: ConversationState) -> dict[str, Any]:
        """תגובת ה-API: הסטטוס האמיתי, ומצב התוצאות המעודכן (copay_pending) כדי שהלקוח יזהה הצלחה ממשית
        ולא רק מהודעת טקסט."""
        return {"status": result.status, "missing_field": result.missing_field,
                "question_id": result.question_id or None, "option_id": result.option_id,
                "car_label": result.car_label, "catalog_amount": result.catalog_amount,
                "question_text": result.question_text,
                "results": [r.model_dump(mode="json") for r in state.results]}

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
                if self.email_notifications is not None:
                    conversation = repo.get(conv_id)
                    if conversation is not None:
                        self.email_notifications.prepare_answer(session, conversation, event.question_id)
                if outcome.reply:
                    repo.add_message(conv_id, "assistant", outcome.reply, "rep",
                                     rep_question_id=event.question_id)
                _store_runs(repo, conv_id, snapshot_id, pending)
            if self.email_notifications is not None:
                self.email_notifications.process_pending()
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
        questions = self.cases.list_for_conversation(str(conv_id))
        pending = [{"missing_field": q.missing_field.value, "mode": q.mode.value, "status": q.status.value,
                    "option_id": q.option_id or None}
                   for q in self.cases.list_for_conversation(str(conv_id), QuestionStatus.OPEN)]
        return {"conversation_id": str(conv_id), "stage": state.stage.value, "messages": messages,
                "pending_questions": pending, "expects": build_expects(state, self.rules.current),
                "results": [r.model_dump(mode="json") for r in state.results],
                "results_stale": results_stale(state), "option_map": map_json(state),
                "rep_questions": [_rep_question_json(q) for q in questions], **map_readiness(state)}

    def view(self, conversation_id: str, token: str) -> ConversationView:
        self._apply_pending(self._authorized(conversation_id, token))
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            state = ConversationState.model_validate(row.state)
            messages = [_message_json(m) for m in repo.messages(row.id)]
            return ConversationView(conversation_id=str(row.id), stage=state.stage, state=state,
                                    messages=messages, expects=build_expects(state, self.rules.current),
                                    rep_questions=[_rep_question_json(q) for q in
                                                   self.cases.list_for_conversation(str(row.id))])

    def _load(self, repo: ConversationRepository, conversation_id: str, token: str):
        row = repo.get(_uuid(conversation_id))
        if row is None or not token or not any(
            hmac.compare_digest(saved, token_hash(token))
            for saved in [row.session_token_hash, *(row.additional_session_token_hashes or [])]
        ):
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
    "ChatMessage", "LLMError", "EmailNotificationService", "EmailNotConfigured", "InvalidEmailLink",
    "ConversationConflict", "ConversationLimitReached", "ConversationNotFound", "ConversationService",
    "ConversationState",
    "ConversationView", "LLMChain", "LLMPort", "OptionResult", "RepReason", "Stage",
    "TurnResult", "build_llm_chain", "map_json", "map_readiness", "results_stale", "reproduce_run", "ChoiceInvalid",
    "FieldsInvalid",
]
