"""ה-API של נציג התחבורה: רשימת שאלות, שאלה אחת, ותשובה.

הרשאה: כותרת X-Rep-Token מול REP_ACCESS_TOKEN (השוואה בזמן קבוע). הנציג רואה רק את ההקשר
של השאלה — אף פעם לא שכר, פרופיל או מזהה השיחה. ערכי תשובות והערות לא נרשמים בלוג.
"""
from __future__ import annotations

import hmac
from datetime import date
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.modules.cases.public import (
    DEFAULT_REP_NAME,
    AnswerInvalid,
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
    """בודק הרשאה והגבלת קצב, ומחזיר את שם הנציג (לשדה answered_by)."""
    ctx = context(request)
    expected = ctx.settings.rep_access_token
    if not expected or not x_rep_token or not hmac.compare_digest(x_rep_token.encode(), expected.encode()):
        raise HTTPException(401, "נדרשת הרשאת נציג")
    client = request.client.host if request.client else "unknown"
    if not ctx.rep_limiter.allow(client):
        raise HTTPException(429, "יותר מדי בקשות. נסו שוב בעוד דקה.")
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
    try:
        return question_json(context(request).cases.get(question_id))
    except QuestionNotFound:
        raise HTTPException(404, NOT_FOUND) from None


@router.post("/questions/{question_id}/answer")
def answer_question(question_id: str, body: AnswerIn, request: Request,
                    name: str = Depends(rep_name)) -> dict[str, Any]:
    effective: Optional[date] = None
    if body.effective_date:
        try:
            effective = date.fromisoformat(body.effective_date.strip())
        except ValueError:
            raise HTTPException(422, "תאריך תחילה לא תקין (YYYY-MM-DD)") from None
    try:
        view = context(request).cases.answer(question_id, body.value, effective_date=effective,
                                             note=body.note, answered_by=name)
    except QuestionNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except QuestionAlreadyAnswered:
        raise HTTPException(409, "השאלה כבר נענתה") from None
    except AnswerInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    return question_json(view)
