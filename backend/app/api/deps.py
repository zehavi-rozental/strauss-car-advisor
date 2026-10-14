"""נקודת ההרכבה: כאן נבנים המנוע למסד, ההגדרות, שרשרת מודלי השפה והשירות."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import sessionmaker

from app.modules.cases.public import CasesService
from app.modules.conversation.public import ConversationService
from app.modules.rules.public import RulesStore
from app.shared.settings import Settings

from .ratelimit import RateLimiter


@dataclass
class AppContext:
    settings: Settings
    sessions: sessionmaker
    rules: RulesStore
    service: ConversationService
    cases: CasesService
    message_limiter: RateLimiter
    create_limiter: RateLimiter
    rep_limiter: RateLimiter


def context(request: Request) -> AppContext:
    return request.app.state.ctx
