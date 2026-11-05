"""נקודות הקצה. כאן רק HTTP: עוגייה, הגבלת קצב, סריאליזציה. ההיגיון במודולים."""
from __future__ import annotations

import hmac
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text as sql

from app.modules.calculation.public import ENGINE_VERSION
from app.modules.conversation.public import (
    ChoiceInvalid,
    ConsentInvalid,
    ConversationConflict,
    ConversationLimitReached,
    ConversationNotFound,
    ConversationService,
    FieldsInvalid,
    EmailNotConfigured,
    InvalidEmailLink,
    RepQuestionInvalid,
    StaleEdit,
    TurnResult,
    engine_state,
    facts_view,
    map_json,
    map_readiness,
    results_stale,
)
from app.modules.payslip.public import PayslipError

from .deps import context
from .ratelimit import client_address

router = APIRouter(prefix="/api")

COOKIE = "sca_session"
NOT_FOUND = "השיחה לא נמצאה"


def _clean_text(value: str) -> str:
    """A NUL or a lone surrogate cannot be stored or encoded: they are dropped here, not turned into a server error."""
    return value.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")


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
    # one value edited on its own ("הנתונים שלי"), through the same gates
    eligibility_group: Optional[str] = None
    occupation: Optional[str] = None
    current_copay: Optional[Decimal] = None
    current_use_value: Optional[Decimal] = None
    current_maintenance: Optional[Decimal] = None
    current_car: Optional[str] = None

    @field_validator("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
                     "current_copay", "current_use_value", "current_maintenance")
    @classmethod
    def _an_ordinary_number(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        """"1e-999990" is a valid Decimal of a million characters: it would be stored and sent to the model on every later call."""
        if value is not None and (not value.is_finite() or value.adjusted() > 9 or value.as_tuple().exponent < -6):
            raise ValueError("not an ordinary number")
        return value


class MessageIn(BaseModel):
    """בדיוק אחד מהארבעה: טקסט חופשי, בחירה אחת, כמה בחירות (חלופות), או טופס שדות פרופיל."""
    text: Optional[str] = None
    choice: Optional[str] = None
    choices: Optional[list[str]] = None
    fields: Optional[ProfileFieldsIn] = None
    field: Optional[str] = None      # בשאלה מקובצת: לאיזה שדה הבחירה (כשכמה שדות מציעים אותו מזהה)
    source: Optional[Literal["payslip"]] = None   # fields only: values approved from a reviewed payslip
    @field_validator("text")
    @classmethod
    def _printable(cls, value: Optional[str]) -> Optional[str]:
        return _clean_text(value) if value is not None else None

    base_version: Optional[int] = None    # fields only: the data version the screen showed (409 if it changed since)


def _turn(t: TurnResult, service: ConversationService) -> dict[str, Any]:
    return {
        "conversation_id": t.conversation_id,
        "stage": t.stage.value,
        "reply": t.reply,
        "source": t.source,
        # what really answered this turn (the model / templates after a failure / none configured); null: code alone
        "engine": engine_state(service.llm, t.say),
        "results": [{"option_id": r.option_id, "label": r.label, "run_id": r.run_id, **r.result}
                    for r in t.results],
        "pending_rep": t.pending_rep.value if t.pending_rep else None,
        "expects": t.expects,
        "results_stale": t.results_stale,
        "results_version": t.results_version,
        "facts": t.facts,
        "card": t.card,
        "privacy": t.privacy,
    }


def _service(request: Request) -> ConversationService:
    return context(request).service


def _authorized(request: Request, conversation_id: str) -> str:
    """The session of this conversation, or 404. Checked before any budget is counted: a stranger cannot use up the budget of
    someone else's conversation, or grow the limiter with made-up ids. Wrong tries are counted per address."""
    ctx = context(request)
    client = client_address(request, ctx.settings.edge_proxy_secret)
    if ctx.auth_failures.full(client):
        raise HTTPException(429, "יותר מדי בקשות. נסו שוב בעוד דקה.")
    try:
        ctx.service.authorize(conversation_id, request.cookies.get(COOKIE, ""))
    except ConversationNotFound:
        ctx.auth_failures.hit(client)
        raise HTTPException(404, NOT_FOUND) from None
    return client


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
    out = {"status": "ok" if db_ok else "degraded", "db": db_ok, "rules_version": calc.rules_version,
           "extensions_version": calc.extensions_version, "engine_version": ENGINE_VERSION,
           "engine": engine_state(ctx.service.llm), "payslip": ctx.payslip.enabled}
    # the models, their last call and any error are for whoever operates the service, not for every visitor
    expected = ctx.settings.rep_access_token
    sent = request.headers.get("x-rep-token", "")
    client = client_address(request, ctx.settings.edge_proxy_secret)
    good = bool(expected and sent) and ctx.rep_gate.locked(client) is None and hmac.compare_digest(sent.encode(), expected.encode())
    if sent and not good:
        ctx.rep_gate.failed(client)      # the same count of wrong codes as the rep's screen: this is no way to guess the code
    if good:
        out.update(config_error=ctx.rules.last_error, llm=ctx.service.llm.mode, llm_mode=ctx.service.agent.mode,
                   llm_chain=ctx.service.llm.status())
    return out


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
    client = client_address(request, ctx.settings.edge_proxy_secret)
    if not ctx.create_limiter.allow(client):
        raise HTTPException(429, "יותר מדי שיחות חדשות. נסו שוב בעוד דקה.")
    turn, token = ctx.service.start()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=ctx.settings.session_cookie_secure,
                        path="/api")
    return _turn(turn, ctx.service)


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ai_processing", "payslip"]
    accepted: bool
    version: str = ""


@router.post("/conversations/{conversation_id}/consent")
def consent(conversation_id: str, body: ConsentIn, request: Request) -> dict[str, Any]:
    """The employee's answer to a notice: its version and the date are kept, nothing else. Withdrawn, the model stops."""
    conversation_id = conversation_id.strip()
    _authorized(request, conversation_id)
    try:
        return _service(request).set_consent(conversation_id, request.cookies.get(COOKIE, ""), body.kind, body.accepted,
                                             body.version)
    except ConsentInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except ConversationConflict:
        raise HTTPException(409, "הפעולה לא נשמרה עקב פעולה מקבילה. נסו שוב.") from None


@router.get("/conversations/{conversation_id}/status")
def conversation_status(conversation_id: str, request: Request) -> dict[str, Any]:
    """Whether this browser's saved conversation holds anything, and nothing of what it holds: the resume screen."""
    try:
        return _service(request).status(conversation_id.strip(), request.cookies.get(COOKIE, ""))
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, request: Request) -> Response:
    """Deletes this conversation and everything of it from this server, and the session cookie from this browser."""
    client = client_address(request, context(request).settings.edge_proxy_secret)
    if context(request).auth_failures.full(client):
        raise HTTPException(429, "יותר מדי בקשות. נסו שוב בעוד דקה.")
    try:
        _service(request).delete(conversation_id.strip(), request.cookies.get(COOKIE, ""))
    except ConversationNotFound:
        context(request).auth_failures.hit(client)
        raise HTTPException(404, NOT_FOUND) from None
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/api")
    return response


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
    client = _authorized(request, conversation_id)
    if not ctx.message_limiter.allow(conversation_id) or not ctx.address_limiter.allow(client):
        raise HTTPException(429, "יותר מדי הודעות בדקה. נסו שוב עוד מעט.")
    try:
        if form is not None:
            return _turn(ctx.service.send_fields(conversation_id, token, form, body.source, body.base_version), ctx.service)
        if choice is not None:
            return _turn(ctx.service.send_choice(conversation_id, token, choice, body.field), ctx.service)
        return _turn(ctx.service.send(conversation_id, token, text), ctx.service)
    except FieldsInvalid as exc:
        # all or nothing: every field outside its range, so the form can mark each one and keep the input
        return JSONResponse({"detail": str(exc), "message_he": str(exc), "errors": exc.errors}, status_code=422)
    except ChoiceInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except ConversationLimitReached:
        raise HTTPException(429, "הגעתם למספר ההודעות המרבי בשיחה. אפשר לפתוח שיחה חדשה.") from None
    except StaleEdit:
        raise HTTPException(409, "הנתונים השתנו בינתיים, ולכן לא שמרתי. בדקו את הערך ונסו שוב.") from None
    except ConversationConflict:
        raise HTTPException(409, "ההודעה הקודמת עדיין בטיפול") from None


@router.get("/conversations/{conversation_id}/updates")
def get_updates(conversation_id: str, request: Request, after: int = 0) -> dict[str, Any]:
    """לתשאול כל כמה שניות: הודעות אחרי seq, שלב, שאלות לנציג שעדיין פתוחות ותוצאות אחרונות."""
    conversation_id = conversation_id.strip()
    try:
        result = _service(request).updates(conversation_id, request.cookies.get(COOKIE, ""), after)
        return {**result, **_email_fields(request, conversation_id)}
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None


class RepQuestionIn(BaseModel):
    """פנייה יזומה של העובד לנציג. intent קובע את הסיווג, לא בחירת רכב/סכום; השרת מאמת את הרכב.
    'copay_verification' נשלח רק מפעולת בירור השתתפות מובנית לרכב ידוע — אחרת 'general' (אינפורמטיבי)."""
    model_config = ConfigDict(extra="forbid")
    question: str
    intent: Literal["general", "copay_verification", "current_copay"] = "general"

    @field_validator("question")
    @classmethod
    def _printable(cls, value: str) -> str:
        return _clean_text(value)

    option_id: Optional[str] = None
    email: Optional[str] = None


@router.post("/conversations/{conversation_id}/rep-question")
def rep_question(conversation_id: str, body: RepQuestionIn, request: Request) -> dict[str, Any]:
    """שאלה שהעובד יוזם לנציג, דרך מנגנון הפניות הקיים. אימות: עוגיית הסשן; הגבלת קצב כמו הודעה רגילה.
    הסיווג (general / copay_verification) מאומת בשרת מול מצב השיחה; תגובה: created / exists עם הנוסח השמור."""
    conversation_id = conversation_id.strip()
    ctx = context(request)
    client = _authorized(request, conversation_id)
    if not ctx.message_limiter.allow(conversation_id) or not ctx.address_limiter.allow(client):
        raise HTTPException(429, "יותר מדי בקשות בדקה. נסו שוב עוד מעט.")
    if body.email and not ctx.email_verify_limiter.allow(client):
        raise HTTPException(429, "יותר מדי בקשות אימות. נסו שוב מאוחר יותר.")
    token = request.cookies.get(COOKIE, "")
    option_id = (body.option_id or "").strip() or None
    try:
        return ctx.service.rep_question(conversation_id, token, body.question, body.intent, option_id, body.email)
    except RepQuestionInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    except UnicodeError:
        raise HTTPException(422, "הטקסט מכיל תווים לא תקינים.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except EmailNotConfigured as exc:
        raise HTTPException(503, "שליחת אימייל אינה מוגדרת כרגע. אפשר להמשיך בשיחה בלי פנייה לנציג.") from exc
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except ConversationConflict:
        raise HTTPException(409, "הפנייה לא נשמרה עקב פעולה מקבילה. נסו שוב.") from None


def _email_fields(request: Request, conversation_id: str) -> dict[str, Any]:
    """The employee's own email state, read once per request (after the cookie was checked by the caller)."""
    email = _service(request).email_status(conversation_id)
    return {"rep_email": email["email"], "rep_email_verified": email["verified"],
            "rep_email_delivery": email["delivery"], "rep_email_pending": email["pending_address"],
            # the fake backend (a local test server): nothing leaves the machine, and the screen must say so
            "rep_email_simulated": email.get("simulated", False)}


class EmailVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = None       # none: the address this conversation is waiting to verify


@router.post("/conversations/{conversation_id}/email/verify")
def resend_email_verification(conversation_id: str, body: EmailVerifyIn, request: Request) -> dict[str, Any]:
    """A new verification link for this conversation's address (the previous one expired or was not delivered).
    Session cookie only; rate-limited; no new link while one is still valid."""
    conversation_id = conversation_id.strip()
    client = _authorized(request, conversation_id)
    if (not context(request).message_limiter.allow(f"email-verify:{conversation_id}")
            or not context(request).email_verify_limiter.allow(client)):
        raise HTTPException(429, "יותר מדי בקשות בדקה. נסו שוב עוד מעט.")
    try:
        return _service(request).resend_verification(conversation_id, request.cookies.get(COOKIE, ""), body.email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except EmailNotConfigured:
        raise HTTPException(503, "עדכונים באימייל אינם זמינים כרגע. תשובת הנציג תופיע בשיחה.") from None
    except ConversationNotFound:
        raise HTTPException(404, NOT_FOUND) from None


class EmailLinkIn(BaseModel):
    token: str


@router.post("/conversations/return")
def return_to_conversation(body: EmailLinkIn, request: Request, response: Response) -> dict[str, Any]:
    """One-time email proof. A verification link verifies; it opens the conversation only where it is already open. An answer link
    opens it here, and every other browser's session stops (EmailNotificationService.redeem)."""
    client = client_address(request, context(request).settings.edge_proxy_secret)
    if not context(request).message_limiter.allow(f"email-return:{client}"):
        raise HTTPException(429, "יותר מדי ניסיונות. נסו שוב בעוד דקה.")
    try:
        conversation_id, token, question_id, verified_only = context(request).service.redeem_email_link(
            body.token, request.cookies.get(COOKIE, ""))
    except InvalidEmailLink:
        raise HTTPException(410, "הקישור אינו תקף או שכבר נוצל. בקשו קישור חדש דרך השיחה המקורית.") from None
    if verified_only:
        return {"conversation_id": None, "question_id": None, "verified_only": True}      # the address is verified, and nothing more
    if token is not None:
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                            secure=context(request).settings.session_cookie_secure, path="/api")
    return {"conversation_id": conversation_id, "question_id": question_id, "verified_only": False}


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
async def payslip_extract(request: Request, file: UploadFile, conversation_id: str = Form(""),
                          consent_version: str = Form("")) -> dict[str, Any]:
    """העלאת תלוש לחילוץ בלבד: מחזיר את השדות הנתמכים שזוהו, לסקירה ואישור בצד הלקוח.
    הקובץ לא נשמר בשרת. השמירה בפרופיל נעשית אחר כך דרך POST .../messages {fields}, רק אחרי אישור העובד.
    A conversation's session and the notice accepted for this upload are required: each extraction is a paid call that sends
    data out, so it is neither open to anyone nor done without the employee's yes."""
    ctx = context(request)
    if not ctx.payslip.enabled:
        raise HTTPException(503, "העלאת תלוש אינה זמינה כרגע. אפשר למלא את הנתונים ידנית.")
    client = _authorized(request, conversation_id.strip())
    if not ctx.payslip_limiter.allow(client):
        raise HTTPException(429, "יותר מדי העלאות. נסו שוב בעוד דקה.")
    if ctx.service.policy.payslip_consent_required:
        # a test environment (sample payslips only) or a reader outside the organization: the employee's explicit yes,
        # in the version on screen; an approved organization reader needs none (the screen still says who reads it)
        try:
            ctx.service.set_consent(conversation_id.strip(), request.cookies.get(COOKIE, ""), "payslip", True,
                                    consent_version)
        except ConsentInvalid as exc:
            raise HTTPException(422, str(exc)) from None
    data = await _read_capped(file, ctx.settings.payslip_max_bytes)
    try:
        # reading a file and asking the model take seconds: never on the loop that serves every other visitor
        result = await run_in_threadpool(ctx.payslip.extract, data)
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
            "today_copay": state["current"]["copay"],          # the same field /updates carries (31/10)
            "today_car": state["current"].get("label") or None,   # the card above the comparison (1/11)
            "today_copay_source": state["current"].get("copay_source") if state["current"]["copay"] is not None else None,
            "today_copay_provisional": (state["current"].get("copay_provisional")
                                        if state["current"]["copay"] is None else None),
            "selected": state["selected"], "results": state["results"],
            "facts": facts_view(view.state, context(request).rules.current), "data_version": view.state.data_version,
            "results_version": view.state.results_version, "privacy": _service(request).privacy_json(view.state),
            "results_stale": results_stale(view.state), "option_map": map_json(view.state),
            **map_readiness(view.state),
            "pending_rep": state["pending_rep"]["reason"] if state["pending_rep"] else None,
            "expects": view.expects, "messages": view.messages, "rep_questions": view.rep_questions,
            **_email_fields(request, conversation_id)}
