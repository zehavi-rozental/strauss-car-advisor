"""נקודות הקצה. כאן רק HTTP: עוגייה, הגבלת קצב, סריאליזציה. ההיגיון במודולים."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text as sql

from app.modules.calculation.public import ENGINE_VERSION
from app.modules.conversation.public import (
    ChoiceInvalid,
    ConversationConflict,
    ConversationLimitReached,
    ConversationNotFound,
    ConversationService,
    FieldsInvalid,
    TurnResult,
    results_stale,
)
from app.modules.payslip.public import PayslipError

from .deps import context

router = APIRouter(prefix="/api")

COOKIE = "sca_session"
NOT_FOUND = "השיחה לא נמצאה"


class ProfileFieldsIn(BaseModel):
    """טופס Q2 / תיקון נתונים: רק מה שנשלח נשמר. הטווחים נבדקים בשירות מול extensions.yaml."""
    model_config = ConfigDict(extra="forbid")
    base_salary: Optional[Decimal] = None          # from a reviewed payslip (or a correction)
    commissions: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    pension_percent: Optional[Decimal] = None
    study_fund_percent: Optional[Decimal] = None
    credit_points: Optional[Decimal] = None
    children_under_23: Optional[int] = None


class MessageIn(BaseModel):
    """בדיוק אחד מהארבעה: טקסט חופשי, בחירה אחת, כמה בחירות (חלופות), או טופס שדות פרופיל."""
    text: Optional[str] = None
    choice: Optional[str] = None
    choices: Optional[list[str]] = None
    fields: Optional[ProfileFieldsIn] = None
    field: Optional[str] = None      # בשאלה מקובצת: לאיזה שדה הבחירה (כשכמה שדות מציעים אותו מזהה)
    source: Optional[Literal["payslip"]] = None   # fields only: values approved from a reviewed payslip


def _turn(t: TurnResult) -> dict[str, Any]:
    return {
        "conversation_id": t.conversation_id,
        "stage": t.stage.value,
        "reply": t.reply,
        "source": t.source,
        "results": [{"option_id": r.option_id, "label": r.label, "run_id": r.run_id, **r.result}
                    for r in t.results],
        "pending_rep": t.pending_rep.value if t.pending_rep else None,
        "expects": t.expects,
        "results_stale": t.results_stale,
    }


def _service(request: Request) -> ConversationService:
    return context(request).service


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    ctx = context(request)
    db_ok = True
    try:
        with ctx.sessions() as session:
            session.execute(sql("SELECT 1"))
    except Exception:
        db_ok = False
    calc = ctx.rules.current.calc
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "rules_version": calc.rules_version,
            "extensions_version": calc.extensions_version, "engine_version": ENGINE_VERSION,
            "config_error": ctx.rules.last_error, "llm": ctx.service.llm.mode, "llm_mode": ctx.service.agent.mode,
            "llm_chain": ctx.service.llm.status(), "payslip": ctx.payslip.enabled}


@router.get("/catalog")
def catalog(request: Request) -> list[dict[str, Any]]:
    return [{"code": c.catalog_code, "label": c.label, "group": c.group, "drive": c.drive.value,
             "use_value": str(c.use_value), "default_copay": str(c.default_copay)}
            for c in context(request).rules.current.catalog]


@router.get("/catalog/search")
def catalog_search(request: Request, q: str = "") -> list[dict[str, Any]]:
    """לבחירת הרכב הנוכחי: הקטלוג קודם, אחר כך מאגר רשות המסים. עד 10; פחות מ-2 תווים → []."""
    return context(request).service.search_cars(q[:80])


@router.get("/occupations")
def occupations(request: Request) -> list[dict[str, Any]]:
    return [{"name": o.name, "group": o.group, "car_mandatory": o.car_mandatory,
             "opt_out_allowed": o.opt_out_allowed}
            for o in context(request).rules.current.occupations.values()]


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, response: Response) -> dict[str, Any]:
    ctx = context(request)
    client = request.client.host if request.client else "unknown"
    if not ctx.create_limiter.allow(client):
        raise HTTPException(429, "יותר מדי שיחות חדשות. נסו שוב בעוד דקה.")
    turn, token = ctx.service.start()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=ctx.settings.session_cookie_secure,
                        path="/api")
    return _turn(turn)


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: MessageIn, request: Request) -> Any:
    conversation_id = conversation_id.strip()
    ctx = context(request)
    given = [x for x in (body.text, body.choice, body.choices, body.fields) if x is not None]
    if len(given) != 1:
        raise HTTPException(422, "יש לשלוח אחד מהשדות: text, choice, choices או fields")
    if body.source is not None and body.fields is None:
        raise HTTPException(422, "source מתאים רק לשליחת שדות")
    form = body.fields.model_dump(exclude_none=True) if body.fields is not None else None
    choice = [body.choice] if body.choice is not None else body.choices
    text = (body.text or "").strip()
    if choice is None and form is None and not text:
        raise HTTPException(422, "ההודעה ריקה")
    if len(text) > ctx.settings.max_message_chars:
        raise HTTPException(422, f"ההודעה ארוכה מדי (עד {ctx.settings.max_message_chars} תווים)")
    if choice is not None and (not choice or len(choice) > 20 or any(len(c) > 80 for c in choice)):
        raise HTTPException(422, "הבחירה אינה מהאפשרויות שהוצעו")
    token = request.cookies.get(COOKIE, "")
    if not ctx.message_limiter.allow(conversation_id):
        raise HTTPException(429, "יותר מדי הודעות בדקה. נסו שוב עוד מעט.")
    try:
        if form is not None:
            return _turn(ctx.service.send_fields(conversation_id, token, form, body.source))
        if choice is not None:
            return _turn(ctx.service.send_choice(conversation_id, token, choice, body.field))
        return _turn(ctx.service.send(conversation_id, token, text))
    except FieldsInvalid as exc:
        # all or nothing: every field outside its range, so the form can mark each one and keep the input
        return JSONResponse({"detail": str(exc), "message_he": str(exc), "errors": exc.errors}, status_code=422)
    except ChoiceInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except ConversationLimitReached:
        raise HTTPException(429, "הגעתם למספר ההודעות המרבי בשיחה. אפשר לפתוח שיחה חדשה.") from None
    except ConversationConflict:
        raise HTTPException(409, "ההודעה הקודמת עדיין בטיפול") from None


@router.get("/conversations/{conversation_id}/updates")
def get_updates(conversation_id: str, request: Request, after: int = 0) -> dict[str, Any]:
    """לתשאול כל כמה שניות: הודעות אחרי seq, שלב, שאלות לנציג שעדיין פתוחות ותוצאות אחרונות."""
    conversation_id = conversation_id.strip()
    try:
        return _service(request).updates(conversation_id, request.cookies.get(COOKIE, ""), after)
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None


async def _read_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """קורא את הקובץ בנתחים עד התקרה. קובץ גדול מהתקרה → 413 בלי להחזיק הכול בזיכרון."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 256)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"הקובץ גדול מדי. אפשר להעלות עד {max_bytes // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/payslip/extract")
async def payslip_extract(request: Request, file: UploadFile) -> dict[str, Any]:
    """העלאת תלוש לחילוץ בלבד: מחזיר את השדות הנתמכים שזוהו, לסקירה ואישור בצד הלקוח.
    הקובץ לא נשמר בשרת. השמירה בפרופיל נעשית אחר כך דרך POST .../messages {fields}, רק אחרי אישור העובד."""
    ctx = context(request)
    if not ctx.payslip.enabled:
        raise HTTPException(503, "העלאת תלוש אינה זמינה כרגע. אפשר למלא את הנתונים ידנית.")
    client = request.client.host if request.client else "unknown"
    if not ctx.payslip_limiter.allow(client):
        raise HTTPException(429, "יותר מדי העלאות. נסו שוב בעוד דקה.")
    data = await _read_capped(file, ctx.settings.payslip_max_bytes)
    try:
        result = ctx.payslip.extract(data)
    except PayslipError as exc:
        raise HTTPException(422, exc.message_he) from None
    finally:
        data = b""   # לא נשארים עם תוכן התלוש בזיכרון יותר מהנדרש
    return result.as_dict()


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    conversation_id = conversation_id.strip()
    try:
        view = _service(request).view(conversation_id, request.cookies.get(COOKIE, ""))
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    state = view.state.model_dump(mode="json")
    return {"conversation_id": view.conversation_id, "stage": view.stage.value, "profile": state["profile"],
            "current_car": {k: v for k, v in state["current"].items() if k != "choices"},
            "selected": state["selected"], "results": state["results"],
            "results_stale": results_stale(view.state),
            "pending_rep": state["pending_rep"]["reason"] if state["pending_rep"] else None,
            "expects": view.expects, "messages": view.messages}

