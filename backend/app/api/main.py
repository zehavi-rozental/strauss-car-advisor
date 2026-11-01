"""אפליקציית FastAPI. הרצה: uvicorn app.api.main:create_app --factory"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from sqlalchemy.engine import Engine, make_url

from app.modules.cases.public import CasesService
from app.modules.conversation.public import (
    ConversationService,
    EmailNotificationService,
    LLMChain,
    build_llm_chain,
)
from app.modules.payslip.public import PayslipService
from app.modules.rules.public import RulesStore
from app.shared.db import get_engine, session_factory
from app.shared.settings import ENV_FILE, Settings, get_settings

from .deps import AppContext
from .ratelimit import RateLimiter, RepGate
from .rep_routes import router as rep_router
from .routes import router


log = logging.getLogger(__name__)


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
    payslip = PayslipService(rules, settings)
    stop_email_worker = threading.Event()
    email_worker: Optional[threading.Thread] = None

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

    def stop_worker() -> None:
        stop_email_worker.set()
        if email_worker is not None:
            email_worker.join(timeout=2)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        start_email_worker()
        try:
            yield
        finally:
            stop_worker()

    app = FastAPI(title="Strauss Car Advisor", lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings, sessions=sessions, rules=rules,
        service=ConversationService(sessions, rules, llm, cases, settings.max_messages_per_conversation,
                                    settings.llm_mode, email_notifications),
        cases=cases,
        payslip=payslip,
        message_limiter=RateLimiter(settings.rate_limit_per_minute),
        create_limiter=RateLimiter(settings.create_rate_limit_per_minute),
        rep_gate=RepGate(settings.rep_read_limit_per_minute, settings.rep_write_limit_per_minute,
                         settings.rep_write_address_limit_per_minute, settings.rep_auth_failure_limit_per_minute),
        payslip_limiter=RateLimiter(settings.payslip_rate_limit_per_minute),
    )
    app.include_router(router)
    app.include_router(rep_router)
    return app
