"""אפליקציית FastAPI. הרצה: uvicorn app.api.main:create_app --factory"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from app.modules.conversation.public import (
    ConversationService,
    LLMChain,
    RepresentativePort,
    build_llm_chain,
)
from app.modules.rules.public import RulesStore
from app.shared.db import get_engine, session_factory
from app.shared.settings import Settings, get_settings

from .deps import AppContext
from .ratelimit import RateLimiter
from .routes import router


def create_app(settings: Optional[Settings] = None, *, engine: Optional[Engine] = None,
               llm: Optional[LLMChain] = None, rep: Optional[RepresentativePort] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    rules = RulesStore(settings.config_dir, settings.config_mode)
    rules.current  # טעינה מיידית: במצב strict קובץ שגוי עוצר את העלייה
    sessions = session_factory(engine or get_engine())
    llm = llm if llm is not None else build_llm_chain(settings)

    app = FastAPI(title="Strauss Car Advisor")
    app.state.ctx = AppContext(
        settings=settings, sessions=sessions, rules=rules,
        service=ConversationService(sessions, rules, llm, rep, settings.max_messages_per_conversation),
        message_limiter=RateLimiter(settings.rate_limit_per_minute),
        create_limiter=RateLimiter(settings.create_rate_limit_per_minute),
    )
    app.include_router(router)
    return app
