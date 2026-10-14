"""הממשק הציבורי של מודול השאלות לנציג התחבורה.

open_question רץ בתוך הטרנזקציה של מי שקורא לו (שלב הכתיבה של התור בשיחה), כך שתור
שבוטל לא משאיר שאלה. answer רץ בטרנזקציה משלו, ואחרי שהתשובה נשמרה היא מתפרסמת
(QuestionAnswered) למנויים. המודול לא מכיר את מודול השיחה.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.shared.db import utcnow

from .models import RepQuestionRow

log = logging.getLogger(__name__)


class MissingField(str, Enum):
    NEW_CAR_COPAY = "NEW_CAR_COPAY"
    CURRENT_COPAY = "CURRENT_COPAY"
    CURRENT_USE_VALUE = "CURRENT_USE_VALUE"
    OCCUPATION_OPT_OUT = "OCCUPATION_OPT_OUT"
    MAINTENANCE_AMOUNT = "MAINTENANCE_AMOUNT"


class Mode(str, Enum):
    CONFIRM = "CONFIRM"       # התוצאה כבר מוצגת; הנציג מאשר או מתקן
    REQUIRED = "REQUIRED"     # השיחה ממתינה לתשובה


class AnswerType(str, Enum):
    MONEY = "MONEY"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TEXT = "TEXT"


class Status(str, Enum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


# רק מה שהנציג צריך. שכר, סכומי פרופיל, ת"ז ותוכן הודעות — אף פעם.
ALLOWED_CONTEXT_KEYS = frozenset({"group", "car_label", "catalog_code", "suggested_amount", "occupation_text"})
MAX_TEXT = 500
DEFAULT_REP_NAME = "נציג"


class AnswerInvalid(ValueError):
    """תשובה שלא מתאימה לסוג השאלה. ההודעה בעברית, לתצוגה לנציג."""


class QuestionAlreadyAnswered(Exception):
    pass


class QuestionNotFound(Exception):
    pass


@dataclass(frozen=True)
class QuestionRequest:
    missing_field: MissingField
    mode: Mode
    answer_type: AnswerType
    question_text: str
    context: dict[str, Optional[str]]
    suggested_value: Optional[str] = None
    option_id: str = ""


@dataclass(frozen=True)
class QuestionView:
    id: str
    conversation_id: str
    missing_field: MissingField
    mode: Mode
    option_id: str
    context: dict[str, Any]
    question_text: str
    answer_type: AnswerType
    suggested_value: Optional[str]
    answer_value: Optional[str]
    answer_note: Optional[str]
    effective_date: Optional[date]
    status: Status
    created_at: datetime
    answered_at: Optional[datetime]
    answered_by: Optional[str]
    version: int


@dataclass(frozen=True)
class QuestionAnswered:
    """אירוע: נציג ענה. השיחה מיישמת אותו (פעם אחת, גם אם הגיע פעמיים)."""

    conversation_id: str
    question_id: str
    missing_field: MissingField
    option_id: str
    value: str
    effective_date: Optional[date]


def _view(row: RepQuestionRow) -> QuestionView:
    return QuestionView(
        id=str(row.id), conversation_id=str(row.conversation_id), missing_field=MissingField(row.missing_field),
        mode=Mode(row.mode), option_id=row.option_id, context=dict(row.context), question_text=row.question_text,
        answer_type=AnswerType(row.answer_type), suggested_value=row.suggested_value,
        answer_value=row.answer_value, answer_note=row.answer_note, effective_date=row.effective_date,
        status=Status(row.status), created_at=row.created_at, answered_at=row.answered_at,
        answered_by=row.answered_by, version=row.version)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise QuestionNotFound() from exc


def validate_answer(answer_type: AnswerType, value: Any) -> str:
    """בודק ומנרמל את התשובה לפי סוג השאלה. הודעות שגיאה בעברית."""
    raw = "" if value is None else str(value).strip()
    if answer_type == AnswerType.MONEY:
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            raise AnswerInvalid("הסכום חייב להיות מספר") from None
        if not amount.is_finite():
            raise AnswerInvalid("הסכום חייב להיות מספר")
        if amount < 0:
            raise AnswerInvalid("הסכום לא יכול להיות שלילי")
        if amount.as_tuple().exponent < -2:
            raise AnswerInvalid("עד שתי ספרות אחרי הנקודה")
        return str(amount)
    if answer_type == AnswerType.BOOLEAN:
        lowered = raw.lower()
        if lowered in ("כן", "true", "yes", "1"):
            return "true"
        if lowered in ("לא", "false", "no", "0"):
            return "false"
        raise AnswerInvalid("התשובה חייבת להיות כן או לא")
    if answer_type == AnswerType.DATE:
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise AnswerInvalid("תאריך לא תקין (YYYY-MM-DD)") from None
    if not raw:
        raise AnswerInvalid("התשובה ריקה")
    if len(raw) > MAX_TEXT:
        raise AnswerInvalid(f"התשובה ארוכה מדי (עד {MAX_TEXT} תווים)")
    return raw


def _check_context(context: dict[str, Any]) -> None:
    extra = set(context) - ALLOWED_CONTEXT_KEYS
    if extra:
        raise ValueError(f"שדות לא מורשים בהקשר לנציג: {sorted(extra)}")
    if any(v is not None and not isinstance(v, str) for v in context.values()):
        raise ValueError("ערכי ההקשר לנציג חייבים להיות טקסט")


class CasesService:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions
        self._subscribers: list[Callable[[QuestionAnswered], None]] = []

    # ---------- פתיחה (בתוך הטרנזקציה של הקורא) ----------

    def open_question(self, session: Session, conversation_id: str, request: QuestionRequest) -> QuestionView:
        """שאלה פתוחה קיימת לאותה שיחה + שדה + חלופה מוחזרת — לא נוצר כפיל."""
        _check_context(request.context)
        conv = _uuid(conversation_id)
        existing = self._open_row(session, conv, request.missing_field, request.option_id)
        if existing is not None:
            return _view(existing)
        row = RepQuestionRow(
            conversation_id=conv, missing_field=request.missing_field.value, mode=request.mode.value,
            option_id=request.option_id, context=dict(request.context), question_text=request.question_text,
            answer_type=request.answer_type.value, suggested_value=request.suggested_value,
            status=Status.OPEN.value, version=0)
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:          # תור מקביל פתח אותה בדיוק עכשיו (האינדקס הייחודי)
            existing = self._open_row(session, conv, request.missing_field, request.option_id)
            assert existing is not None
            return _view(existing)
        return _view(row)

    @staticmethod
    def _open_row(session: Session, conv: uuid.UUID, field: MissingField, option_id: str) -> Optional[RepQuestionRow]:
        return session.scalar(select(RepQuestionRow).where(
            RepQuestionRow.conversation_id == conv, RepQuestionRow.missing_field == field.value,
            RepQuestionRow.option_id == option_id, RepQuestionRow.status == Status.OPEN.value))

    def cancel(self, session: Session, question_ids: list[str]) -> int:
        """ביטול שאלות פתוחות (למשל כשהעובד תיקן את הרכב). בתוך הטרנזקציה של הקורא."""
        if not question_ids:
            return 0
        ids = [_uuid(q) for q in question_ids]
        result = session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.id.in_(ids), RepQuestionRow.status == Status.OPEN.value)
            .values(status=Status.CANCELLED.value, version=RepQuestionRow.version + 1)
            .execution_options(synchronize_session=False))
        return result.rowcount

    def cancel_for_conversation(self, session: Session, conversation_id: str) -> int:
        result = session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.conversation_id == _uuid(conversation_id),
                   RepQuestionRow.status == Status.OPEN.value)
            .values(status=Status.CANCELLED.value, version=RepQuestionRow.version + 1)
            .execution_options(synchronize_session=False))
        return result.rowcount

    # ---------- קריאה ----------

    def list_questions(self, status: Status = Status.OPEN) -> list[QuestionView]:
        with self.sessions() as session:
            rows = session.scalars(select(RepQuestionRow).where(RepQuestionRow.status == status.value)
                                   .order_by(RepQuestionRow.created_at))
            return [_view(r) for r in rows]

    def list_open(self) -> list[QuestionView]:
        return self.list_questions(Status.OPEN)

    def get(self, question_id: str) -> QuestionView:
        with self.sessions() as session:
            row = session.get(RepQuestionRow, _uuid(question_id))
            if row is None:
                raise QuestionNotFound()
            return _view(row)

    def list_for_conversation(self, conversation_id: str, status: Optional[Status] = None,
                              session: Optional[Session] = None) -> list[QuestionView]:
        stmt = select(RepQuestionRow).where(RepQuestionRow.conversation_id == _uuid(conversation_id))
        if status is not None:
            stmt = stmt.where(RepQuestionRow.status == status.value)
        stmt = stmt.order_by(RepQuestionRow.created_at)
        if session is not None:
            return [_view(r) for r in session.scalars(stmt)]
        with self.sessions() as own:
            return [_view(r) for r in own.scalars(stmt)]

    # ---------- תשובה (טרנזקציה משלה) ----------

    def answer(self, question_id: str, value: Any, effective_date: Optional[date] = None,
               note: Optional[str] = None, answered_by: Optional[str] = None) -> QuestionView:
        """עדכון מותנה: רק שאלה פתוחה, ורק אם הגרסה לא השתנתה. תשובה שנייה → QuestionAlreadyAnswered."""
        note = (note or "").strip() or None
        if note is not None and len(note) > MAX_TEXT:
            raise AnswerInvalid(f"ההערה ארוכה מדי (עד {MAX_TEXT} תווים)")
        name = (answered_by or "").strip()[:60] or DEFAULT_REP_NAME
        with self.sessions() as session, session.begin():
            row = session.get(RepQuestionRow, _uuid(question_id))
            if row is None:
                raise QuestionNotFound()
            if row.status != Status.OPEN.value:
                raise QuestionAlreadyAnswered()
            normalized = validate_answer(AnswerType(row.answer_type), value)
            loaded_version = row.version
            result = session.execute(
                update(RepQuestionRow)
                .where(RepQuestionRow.id == row.id, RepQuestionRow.status == Status.OPEN.value,
                       RepQuestionRow.version == loaded_version)
                .values(status=Status.ANSWERED.value, answer_value=normalized, answer_note=note,
                        effective_date=effective_date, answered_at=utcnow(), answered_by=name,
                        version=loaded_version + 1)
                .execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise QuestionAlreadyAnswered()
            session.expire(row)
            view = _view(session.get(RepQuestionRow, row.id))
        log.info("rep answered question %s field=%s", view.id, view.missing_field.value)
        self.publish(QuestionAnswered(
            conversation_id=view.conversation_id, question_id=view.id, missing_field=view.missing_field,
            option_id=view.option_id, value=normalized, effective_date=effective_date))
        return view

    # ---------- אירועים ----------

    def subscribe(self, handler: Callable[[QuestionAnswered], None]) -> None:
        self._subscribers.append(handler)

    def publish(self, event: QuestionAnswered) -> None:
        """אחרי שהתשובה נשמרה. כשל של מנוי לא מבטל אותה — השיחה תיישם אותה בקריאה הבאה."""
        for handler in self._subscribers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("applying rep answer %s failed (%s); it will be applied on the next read",
                            event.question_id, type(exc).__name__)


__all__ = [
    "ALLOWED_CONTEXT_KEYS", "DEFAULT_REP_NAME", "AnswerInvalid", "AnswerType", "CasesService", "MissingField", "Mode",
    "QuestionAlreadyAnswered", "QuestionAnswered", "QuestionNotFound", "QuestionRequest", "QuestionView",
    "Status", "validate_answer",
]
