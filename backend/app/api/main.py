"""אפליקציית FastAPI. הרצה: uvicorn app.api.main:create_app --factory"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine, make_url

from app.modules.cases.public import CasesService
from app.modules.conversation.public import (
    ConversationService,
    EmailNotificationService,
    LLMChain,
    SessionLocked,
    build_llm_chain,
)
from app.modules.payslip.public import PayslipService
from app.modules.rules.public import RulesStore
from app.shared.db import get_engine, session_factory
from app.shared.ai_policy import policy_from
from app.shared.settings import ENV_FILE, Settings, get_settings

from .deps import AppContext
from .ratelimit import RateLimiter, RepGate
from .rep_routes import router as rep_router
from .routes import router


log = logging.getLogger(__name__)

# the app's own pages: only its own scripts, fonts and data. Inline styles stay allowed for React's style attributes
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                           "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
RETENTION_CHECK_S = 3600.0


def startup_summary(settings: Settings, chain: LLMChain) -> str:
    """שורה אחת: מה נטען בפועל. רק נתיבים ותוויות provider:model — בלי מפתחות ובלי כתובת מסד מלאה."""
    url = make_url(settings.database_url)
    database = (url.database or ":memory:") if url.get_backend_name() == "sqlite" else url.get_backend_name()
    entries = chain.status()
    active = [e["entry"] for e in entries if e["status"] != "disabled"]
    disabled = [e["entry"] for e in entries if e["status"] == "disabled"]
    return (f"startup env_file={ENV_FILE} ({'found' if ENV_FILE.exists() else 'missing'}) "
            f"database={database} llm_chain=[{', '.join(active) or 'templates'}]"
            + (f" disabled=[{', '.join(disabled)}]" if disabled else ""))


class BodySizeLimit:
    """A body larger than any route needs is refused before it is read: by its declared length, and by counting when none is declared."""

    def __init__(self, app, default: int, larger: dict[str, int]):
        self.app, self.default, self.larger = app, default, larger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.larger.get(scope["path"], self.default)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            body = b'{"detail":"\xd7\x94\xd7\x91\xd7\xa7\xd7\xa9\xd7\x94 \xd7\x92\xd7\x93\xd7\x95\xd7\x9c\xd7\x94 \xd7\x9e\xd7\x93\xd7\x99."}'
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            return await send({"type": "http.response.body", "body": body})
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, "הבקשה גדולה מדי.")
            return message

        await self.app(scope, counted, send)


def create_app(settings: Optional[Settings] = None, *, engine: Optional[Engine] = None,
               llm: Optional[LLMChain] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    rules = RulesStore(settings.config_dir, settings.config_mode)
    rules.current  # טעינה מיידית: במצב strict קובץ שגוי עוצר את העלייה
    sessions = session_factory(engine or get_engine())
    llm = llm if llm is not None else build_llm_chain(settings)
    log.info(startup_summary(settings, llm))

    cases = CasesService(sessions)
    email_notifications = EmailNotificationService(sessions, settings)
    policy = policy_from(settings)      # who processes the data, and what that allows (shared.ai_policy)
    payslip = PayslipService(rules, settings, policy=policy)
    stop_email_worker = threading.Event()
    email_worker: Optional[threading.Thread] = None
    retention_worker: Optional[threading.Thread] = None

    def start_email_worker() -> None:
        nonlocal email_worker
        if settings.email_backend != "smtp" or email_worker is not None:
            return

        def run() -> None:
            while not stop_email_worker.wait(30):
                try:
                    email_notifications.process_pending()
                except Exception as exc:
                    log.error("email outbox worker failed (%s)", type(exc).__name__)

        email_worker = threading.Thread(target=run, name="email-outbox", daemon=True)
        email_worker.start()

    def start_retention_worker() -> None:
        """Conversations past their retention are deleted with everything of theirs: at startup, then every hour."""
        nonlocal retention_worker
        if settings.conversation_retention_days <= 0 or retention_worker is not None:
            return

        def run() -> None:
            while True:
                try:
                    app.state.ctx.service.purge_expired()
                except Exception as exc:
                    log.error("retention worker failed (%s)", type(exc).__name__)
                if stop_email_worker.wait(RETENTION_CHECK_S):
                    return

        retention_worker = threading.Thread(target=run, name="retention", daemon=True)
        retention_worker.start()

    def stop_worker() -> None:
        stop_email_worker.set()
        if email_worker is not None:
            email_worker.join(timeout=2)
        if retention_worker is not None:
            retention_worker.join(timeout=2)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        start_email_worker()
        start_retention_worker()
        try:
            yield
        finally:
            stop_worker()

    # no /docs, /redoc or /openapi.json: the service's map is not for visitors
    app = FastAPI(title="Strauss Car Advisor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.add_middleware(BodySizeLimit, default=256 * 1024, larger={"/api/payslip/extract": settings.payslip_max_bytes + 1024 * 1024})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"     # pay data never stays in a cache of a shared computer
        else:
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    @app.exception_handler(SessionLocked)
    async def session_locked(_request: Request, _exc: SessionLocked) -> JSONResponse:
        message = "השיחה ננעלה אחרי זמן ללא פעילות, כדי להגן על הפרטים."
        return JSONResponse({"detail": message, "message_he": message, "code": "session_locked"}, status_code=401)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # the fields only: FastAPI's default echoes the values sent, and these can be pay data
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        message = "הבקשה אינה תקינה. בדקו את הערכים ונסו שוב."
        return JSONResponse({"detail": message, "message_he": message, "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error (%s)", type(exc).__name__)     # the type only: its text may carry anything
        message = "משהו השתבש. נסו שוב בעוד רגע."
        return JSONResponse({"detail": message, "message_he": message}, status_code=500)

    app.state.ctx = AppContext(
        settings=settings, sessions=sessions, rules=rules,
        service=ConversationService(sessions, rules, llm, cases, settings.max_messages_per_conversation,
                                    settings.llm_mode, email_notifications,
                                    require_ai_consent=settings.require_ai_consent,
                                    session_idle_minutes=settings.session_idle_minutes,
                                    retention_days=settings.conversation_retention_days, policy=policy),
        cases=cases,
        payslip=payslip,
        message_limiter=RateLimiter(settings.rate_limit_per_minute),
        create_limiter=RateLimiter(settings.create_rate_limit_per_minute),
        rep_gate=RepGate(settings.rep_read_limit_per_minute, settings.rep_write_limit_per_minute,
                         settings.rep_write_address_limit_per_minute, settings.rep_auth_failure_limit_per_minute),
        payslip_limiter=RateLimiter(settings.payslip_rate_limit_per_minute),
        address_limiter=RateLimiter(settings.message_address_limit_per_minute),
        auth_failures=RateLimiter(settings.auth_failure_limit_per_minute),
        email_verify_limiter=RateLimiter(settings.email_verify_address_limit_per_hour, window_s=3600.0),
    )
    app.include_router(router)
    app.include_router(rep_router)
    return app
