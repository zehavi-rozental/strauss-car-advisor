"""נקודות הקצה. כאן רק HTTP: עוגייה, הגבלת קצב, סריאליזציה. ההיגיון במודולים."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text as sql

from app.modules.calculation.public import ENGINE_VERSION
from app.modules.conversation.public import (
    ChoiceInvalid,
    ConversationConflict,
    ConversationLimitReached,
    ConversationNotFound,
    ConversationService,
    TurnResult,
)

from .deps import context

router = APIRouter(prefix="/api")

COOKIE = "sca_session"
NOT_FOUND = "השיחה לא נמצאה"


class MessageIn(BaseModel):
    """בדיוק אחד מהשלושה: טקסט חופשי, בחירה אחת, או כמה בחירות (חלופות)."""
    text: Optional[str] = None
    choice: Optional[str] = None
    choices: Optional[list[str]] = None


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
            "llm_chain": ctx.service.llm.status()}


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
def send_message(conversation_id: str, body: MessageIn, request: Request) -> dict[str, Any]:
    conversation_id = conversation_id.strip()
    ctx = context(request)
    given = [x for x in (body.text, body.choice, body.choices) if x is not None]
    if len(given) != 1:
        raise HTTPException(422, "יש לשלוח אחד מהשדות: text, choice או choices")
    choice = [body.choice] if body.choice is not None else body.choices
    text = (body.text or "").strip()
    if choice is None and not text:
        raise HTTPException(422, "ההודעה ריקה")
    if len(text) > ctx.settings.max_message_chars:
        raise HTTPException(422, f"ההודעה ארוכה מדי (עד {ctx.settings.max_message_chars} תווים)")
    if choice is not None and (not choice or len(choice) > 20 or any(len(c) > 80 for c in choice)):
        raise HTTPException(422, "הבחירה אינה מהאפשרויות שהוצעו")
    token = request.cookies.get(COOKIE, "")
    if not ctx.message_limiter.allow(conversation_id):
        raise HTTPException(429, "יותר מדי הודעות בדקה. נסו שוב עוד מעט.")
    try:
        if choice is not None:
            return _turn(ctx.service.send_choice(conversation_id, token, choice))
        return _turn(ctx.service.send(conversation_id, token, text))
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
            "pending_rep": state["pending_rep"]["reason"] if state["pending_rep"] else None,
            "expects": view.expects, "messages": view.messages}

