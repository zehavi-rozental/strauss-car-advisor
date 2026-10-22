"""ה-API של נציג התחבורה: רשימת שאלות, שאלה אחת, ותשובה.

הרשאה: כותרת X-Rep-Token מול REP_ACCESS_TOKEN (השוואה בזמן קבוע). הנציג רואה רק את ההקשר
של השאלה — אף פעם לא שכר, פרופיל או מזהה השיחה. ערכי תשובות והערות לא נרשמים בלוג.
"""
from __future__ import annotations

import hmac
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.modules.cases.public import (
    DEFAULT_REP_NAME,
    AnswerInvalid,
    MissingField,
    QuestionAlreadyAnswered,
    QuestionNotFound,
    QuestionView,
    Status,
)

from .deps import context

router = APIRouter(prefix="/api/rep")

NOT_FOUND = "השאלה לא נמצאה"


class AnswerIn(BaseModel):
    value: Any = None
    effective_date: Optional[str] = None
    note: Optional[str] = None


def _header_text(value: Optional[str]) -> str:
    """כותרת בעברית: UTF-8 גולמי (שמגיע כ-latin-1) או מקודד ב-URL ("%D7%93...")."""
    if not value:
        return ""
    try:
        value = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unquote(value)


def rep_name(request: Request, x_rep_token: Optional[str] = Header(None),
             x_rep_name: Optional[str] = Header(None)) -> str:
    """בודק הגבלת קצב ואז הרשאה, ומחזיר את שם הנציג (לשדה answered_by).

    הגבלת הקצב קודמת לבדיקת הטוקן: גם ניסיונות עם טוקן שגוי נספרים, כך שאי אפשר לנחש בלי הגבלה."""
    ctx = context(request)
    client = request.client.host if request.client else "unknown"
    if not ctx.rep_limiter.allow(client):
        raise HTTPException(429, "יותר מדי בקשות. נסו שוב בעוד דקה.")
    expected = ctx.settings.rep_access_token
    if not expected or not x_rep_token or not hmac.compare_digest(x_rep_token.encode(), expected.encode()):
        raise HTTPException(401, "נדרשת הרשאת נציג")
    return _header_text(x_rep_name).strip()[:60] or DEFAULT_REP_NAME


def question_json(q: QuestionView) -> dict[str, Any]:
    """מה שהנציג רואה. בלי conversation_id ובלי שום נתון מהפרופיל."""
    return {
        "id": q.id, "missing_field": q.missing_field.value, "mode": q.mode.value, "option_id": q.option_id or None,
        "question_text": q.question_text, "answer_type": q.answer_type.value,
        "suggested_value": q.suggested_value, "context": q.context, "status": q.status.value,
        "created_at": q.created_at.isoformat(), "answered_at": q.answered_at.isoformat() if q.answered_at else None,
        "answer_value": q.answer_value, "effective_date": q.effective_date.isoformat() if q.effective_date else None,
        "answer_note": q.answer_note, "answered_by": q.answered_by,
    }


@router.get("/questions")
def list_questions(request: Request, status: str = "OPEN", _: str = Depends(rep_name)) -> list[dict[str, Any]]:
    try:
        wanted = Status(status.upper())
    except ValueError:
        raise HTTPException(422, "סטטוס לא מוכר (OPEN, ANSWERED, CANCELLED)") from None
    return [question_json(q) for q in context(request).cases.list_questions(wanted)]


@router.get("/questions/{question_id}")
def get_question(question_id: str, request: Request, _: str = Depends(rep_name)) -> dict[str, Any]:
    question_id = question_id.strip()
    try:
        return question_json(context(request).cases.get(question_id))
    except QuestionNotFound:
        raise HTTPException(404, NOT_FOUND) from None


# The representative's amount meets the same range as the employee's for that value (extensions.yaml
# value_ranges). A new car's copay has no configured range: only the non-negative check of cases applies.
_RANGE_OF = {MissingField.CURRENT_COPAY: "current_copay", MissingField.CURRENT_USE_VALUE: "current_use_value",
             MissingField.MAINTENANCE_AMOUNT: "current_maintenance"}


def _check_range(request: Request, field: MissingField, value: Any) -> None:
    """422 for an amount outside the configured range (a typo such as 350000 for a copay), before anything is
    saved. The amount itself is never echoed or logged; what is not a number is left to cases' own check."""
    key = _RANGE_OF.get(field)
    if key is None:
        return
    bounds = context(request).rules.current.extensions.value_ranges.get(key)
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return
    if bounds is not None and amount.is_finite() and not bounds[0] <= amount <= bounds[1]:
        low, high = (f"{int(b):,}" for b in bounds)
        raise AnswerInvalid(f"הסכום מחוץ לטווח הסביר לשדה הזה ({low}–{high} ₪). בדקו אותו ושלחו שוב.")


@router.post("/questions/{question_id}/answer")
def answer_question(question_id: str, body: AnswerIn, request: Request,
                    name: str = Depends(rep_name)) -> dict[str, Any]:
    question_id = question_id.strip()
    effective: Optional[date] = None
    if body.effective_date:
        try:
            effective = date.fromisoformat(body.effective_date.strip())
        except ValueError:
            raise HTTPException(422, "תאריך תחילה לא תקין (YYYY-MM-DD)") from None
    try:
        _check_range(request, context(request).cases.get(question_id).missing_field, body.value)
        view = context(request).cases.answer(question_id, body.value, effective_date=effective,
                                             note=body.note, answered_by=name)
    except QuestionNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except QuestionAlreadyAnswered:
        raise HTTPException(409, "השאלה כבר נענתה") from None
    except AnswerInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    return question_json(view)
