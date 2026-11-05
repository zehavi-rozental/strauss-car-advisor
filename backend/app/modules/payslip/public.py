"""הממשק הציבורי של מודול התלוש. ה-API מדבר רק דרך כאן.

PayslipService מקבל קובץ (bytes), מוודא אותו, מחלץ שדות נתמכים דרך מודל ראייה, ומחזיר ExtractionResult.
אין שמירה: הקובץ לא נכתב לדיסק ולא נשמר במסד. השמירה בפרופיל נעשית בנפרד דרך מודול השיחה (send_fields),
רק אחרי שהעובד סקר, תיקן ואישר.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from app.modules.rules.public import RulesSnapshot, RulesStore
from app.shared.ai_policy import ProcessingPolicy, policy_from
from app.shared.settings import Settings

from .extract import VisionPort, extract, failed, validate_file
from .fixture_vision import FixtureVision
from .gemini_vision import GeminiVisionAdapter
from .models import (
    FIELD_KIND,
    FIELD_LABEL_HE,
    SUPPORTED_FIELDS,
    ExtractedField,
    ExtractionResult,
    PayslipError,
)

log = logging.getLogger(__name__)

_DISABLED = "העלאת תלוש אינה זמינה כרגע. אפשר למלא את הנתונים ידנית."


def _first_gemini_model(chain: str) -> str:
    """הדגם הראשון מסוג gemini ב-LLM_CHAIN (אותו ספק שהשיחה כבר משתמשת בו). ריק אם אין."""
    for item in chain.split(","):
        provider, _, model = item.strip().partition(":")
        if provider.strip().lower() == "gemini" and model.strip():
            return model.strip()
    return ""


def build_vision(settings: Settings) -> Optional[VisionPort]:
    """מודל הראייה מההגדרות, או None כשהתכונה סגורה / אין מפתח (אז החילוץ עובר למילוי ידני).

    נדרש: PAYSLIP_ENABLED=true ו-GEMINI_API_KEY. הדגם: PAYSLIP_MODEL, אחרת הדגם הראשון מ-LLM_CHAIN.
    """
    if not settings.payslip_enabled:
        return None
    if settings.payslip_fake_vision_dir:
        log.warning("payslip extraction uses RECORDED answers from PAYSLIP_FAKE_VISION_DIR (tests only, no Gemini)")
        return FixtureVision(settings.payslip_fake_vision_dir)
    key = settings.gemini_api_key
    if not key:
        log.warning("payslip enabled but GEMINI_API_KEY is missing; upload will fall back to manual entry")
        return None
    model = settings.payslip_model.strip() or _first_gemini_model(settings.llm_chain)
    if not model:
        log.warning("payslip enabled but no gemini model configured (PAYSLIP_MODEL / LLM_CHAIN)")
        return None
    return GeminiVisionAdapter(model, key, timeout_s=settings.payslip_timeout_s)


class PayslipService:
    """נקודת הכניסה היחידה של ה-API. בונה את מודל הראייה פעם אחת בהפעלה.

    enabled=False כשהתכונה סגורה — ה-API מחזיר 503 בעברית במקום לנסות לחלץ.
    """

    def __init__(self, rules: RulesStore, settings: Settings, vision: Optional[VisionPort] = None,
                 policy: Optional[ProcessingPolicy] = None):
        self.rules = rules
        self.max_bytes = settings.payslip_max_bytes
        self.max_pages = settings.payslip_max_pages
        self.timeout_s = settings.payslip_timeout_s
        self.policy = policy or policy_from(settings)
        # an external reader gets a PDF's pay lines only; a whole file (a photo, a scan) only where the policy allows it
        self.minimize = self.policy.payslip_minimized
        self.allow_full_file = self.policy.payslip_full_file
        self.daily_limit = settings.payslip_daily_limit
        self._day: Optional[date] = None
        self._today = 0
        self._vision = vision if vision is not None else build_vision(settings)

    @property
    def enabled(self) -> bool:
        return self._vision is not None

    def extract(self, data: bytes, snapshot: Optional[RulesSnapshot] = None) -> ExtractionResult:
        """מוודא את הקובץ (PayslipError בעברית לקובץ לא תקין) ומחלץ. הקובץ לא נשמר בשום מקום."""
        mime = validate_file(data, self.max_bytes, self.max_pages)
        snapshot = snapshot or self.rules.current
        day = datetime.now(timezone.utc).date()
        if self._day != day:
            self._day, self._today = day, 0
        self._today += 1
        if self.daily_limit and self._today > self.daily_limit:
            log.warning("payslip daily limit reached")
            return failed("daily_limit")
        return extract(data, mime, self._vision, snapshot, self.timeout_s, self.allow_full_file, self.minimize)


__all__ = [
    "PayslipService",
    "PayslipError",
    "ExtractionResult",
    "ExtractedField",
    "SUPPORTED_FIELDS",
    "FIELD_KIND",
    "FIELD_LABEL_HE",
    "build_vision",
]
