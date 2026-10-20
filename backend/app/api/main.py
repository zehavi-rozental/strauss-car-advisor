"""אפליקציית FastAPI. הרצה: uvicorn app.api.main:create_app --factory"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI
from sqlalchemy.engine import Engine, make_url

from app.modules.cases.public import CasesService
from app.modules.conversation.public import (
    ConversationService,
    LLMChain,
    build_llm_chain,
)
from app.modules.payslip.public import PayslipService
from app.modules.rules.public import RulesStore
from app.shared.db import get_engine, session_factory
from app.shared.settings import ENV_FILE, Settings, get_settings

from .deps import AppContext
from .ratelimit import RateLimiter
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
    payslip = PayslipService(rules, settings)
    app = FastAPI(title="Strauss Car Advisor")
    app.state.ctx = AppContext(
        settings=settings, sessions=sessions, rules=rules,
        service=ConversationService(sessions, rules, llm, cases, settings.max_messages_per_conversation,
                                    settings.llm_mode),
        cases=cases,
        payslip=payslip,
        message_limiter=RateLimiter(settings.rate_limit_per_minute),
        create_limiter=RateLimiter(settings.create_rate_limit_per_minute),
        rep_limiter=RateLimiter(settings.rep_rate_limit_per_minute),
        payslip_limiter=RateLimiter(settings.payslip_rate_limit_per_minute),
    )
    app.include_router(router)
    app.include_router(rep_router)
    return app
