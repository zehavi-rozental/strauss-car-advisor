"""Who processes the employee's data, and what that allows (5/11). Read from the settings, decided here, said to the screen.

The product screens used to say "Google Gemini" and "זו הדגמה, אעלה רק תלוש לדוגמה" in their own code, whatever the
deployment was. Now one policy, built from the settings, says:
- the provider of the conversation and of the payslip reading: its name, and whether it runs outside the organization
  (`external`) or inside it (`organization`). Our Gemini adapter calls Google's public API: always external, whatever a
  setting says;
- the environment: `production` only where the organization approved this deployment and its providers for real
  employee data (`REAL_DATA_APPROVED`); anything else is `test`, and the screens say so (sample payslips only);
- what of a payslip leaves the server: an external provider gets the pay lines of a PDF only (payslip.minimize); a whole
  file (a photo, a scan) only to an approved organization provider, or where a test deployment allows synthetic files;
- which notices are asked: the conversation's notice for an external provider; the payslip notice in a test
  environment and for an external provider. Each notice version names the provider, so a new provider asks again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .settings import Settings

Hosting = Literal["external", "organization", "local"]
EXTERNAL: Hosting = "external"
ORGANIZATION: Hosting = "organization"
LOCAL: Hosting = "local"          # recorded answers on this server (browser tests): nothing is sent anywhere

# provider id in LLM_CHAIN → (full name, short name)
_NAMES = {"gemini": ("Google Gemini", "Gemini"), "groq": ("Groq", "Groq"), "openrouter": ("OpenRouter", "OpenRouter"),
          "openai": ("OpenAI", "OpenAI")}
# adapters that only call a public service: never inside the organization
_PUBLIC_ONLY = {"gemini", "groq", "openrouter"}
NOTICE_SERIES = "2026-11"


@dataclass(frozen=True)
class Provider:
    id: str          # "gemini", "openai", ... ("" when none is configured)
    name: str        # what the notices say: "Google Gemini", or the organization's own name for its model
    short: str       # what the status badge says: "Gemini"
    hosting: Hosting

    @property
    def external(self) -> bool:
        return self.hosting == EXTERNAL

    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", f"{self.id}-{self.name}".lower()).strip("-") or "none"


def _first_provider(chain: str, fallback: str) -> str:
    for item in chain.split(","):
        provider = item.strip().partition(":")[0].strip().lower()
        if provider:
            return provider
    return fallback.strip().lower()


def _provider(pid: str, name: str, hosting: str) -> Provider:
    full, short = _NAMES.get(pid, (pid or "ספק ה-AI", pid or "AI"))      # none configured: a plain word, never a blank
    if name.strip():
        full = short = name.strip()
    place: Hosting = ORGANIZATION if hosting.strip().lower() == ORGANIZATION and pid not in _PUBLIC_ONLY else EXTERNAL
    return Provider(id=pid, name=full, short=short, hosting=place)


@dataclass(frozen=True)
class ProcessingPolicy:
    chat: Provider
    payslip: Provider
    real_data_approved: bool
    allow_full_file_setting: bool
    require_ai_consent_setting: bool

    @property
    def environment(self) -> Literal["production", "test"]:
        return "production" if self.real_data_approved else "test"

    # ---- the conversation ----
    @property
    def ai_consent_required(self) -> bool:
        """An external provider gets the messages only after the employee's yes; an approved organization model is
        covered by the organization's own policy (the screen still says who processes the messages)."""
        return self.require_ai_consent_setting and self.chat.external

    @property
    def ai_notice_version(self) -> str:
        return f"{NOTICE_SERIES}.{self.environment}.{self.chat.hosting}.{self.chat.slug()}"

    def ai_notice(self) -> str:
        if self.chat.external:
            return (f"כדי להבין כתיבה חופשית, ההודעות שלכם והנתונים שמסרתם נשלחים לעיבוד ב-{self.chat.name}, שירות "
                    "מחוץ לארגון. פרטי קשר ומספרי זהות מוסרים לפני השליחה. החישובים נעשים כאן, לא במודל.")
        return (f"ההודעות שלכם מעובדות במודל השפה של הארגון ({self.chat.name}), והנתונים לא יוצאים מהארגון. "
                "החישובים נעשים כאן, לא במודל.")

    # ---- the payslip ----
    @property
    def payslip_minimized(self) -> bool:
        """Only the pay lines leave for an external provider (never the file, never a name, an ID or a bank)."""
        return self.payslip.external

    @property
    def payslip_full_file(self) -> bool:
        """A whole file (a photo, a scan) may be read by the organization's own reader (nothing leaves the organization);
        an external reader gets a whole file only where a deployment turns it on (PAYSLIP_ALLOW_FULL_FILE)."""
        return not self.payslip.external or self.allow_full_file_setting

    @property
    def payslip_formats(self) -> list[str]:
        return ["pdf", "image"] if self.payslip_full_file else ["pdf"]

    @property
    def payslip_consent_required(self) -> bool:
        return self.environment == "test" or self.payslip.external

    @property
    def payslip_notice_version(self) -> str:
        return (f"{NOTICE_SERIES}.{self.environment}.{self.payslip.hosting}.{self.payslip.slug()}"
                f".{'file' if self.payslip_full_file else 'lines'}")

    def payslip_notice(self) -> str:
        if self.payslip.external:
            sent = (f"הקובץ נקרא כאן, ול-{self.payslip.name} נשלחים רק שמות רכיבי השכר והסכומים שלהם. "
                    "שם, ת\"ז, בנק וכתובת לא נשלחים.")
            if not self.payslip_full_file:
                sent += " אפשר להעלות PDF בלבד."
        elif self.payslip.hosting == LOCAL:
            sent = "התלוש נקרא כאן בשרת, ולא נשלח לשום שירות."
        else:
            sent = f"התלוש נקרא במודל של הארגון ({self.payslip.name}), והנתונים לא יוצאים מהארגון."
        if self.environment == "test":
            sent += " זו סביבת בדיקות: מעלים כאן רק תלוש לדוגמה, לא תלוש אמיתי."
        return sent

    def payslip_acknowledgement(self) -> str:
        """The checkbox, when one is needed ("" otherwise)."""
        if not self.payslip_consent_required:
            return ""
        if self.environment == "test":
            return "הבנתי ומאשר/ת: אעלה רק תלוש לדוגמה."
        return f"הבנתי ומאשר/ת לשלוח את רכיבי השכר לעיבוד ב-{self.payslip.name}."

    def privacy_json(self) -> dict[str, object]:
        """The provider part of what the screen says (the conversation adds what was accepted)."""
        return {"environment": self.environment,
                "provider": {"name": self.chat.name, "short": self.chat.short, "hosting": self.chat.hosting},
                "payslip_provider": {"name": self.payslip.name, "short": self.payslip.short,
                                     "hosting": self.payslip.hosting}}


def policy_from(settings: Settings) -> ProcessingPolicy:
    chat_id = _first_provider(settings.llm_chain, settings.llm_provider if settings.llm_provider not in ("fake", "none")
                              else "")
    chat = _provider(chat_id, settings.ai_provider_name, settings.ai_provider_hosting)
    # the payslip reader is the Gemini vision adapter (payslip.public.build_vision); recorded answers in browser tests
    if settings.payslip_fake_vision_dir:
        payslip = Provider(id="recorded", name="תשובות מוקלטות", short="מוקלט", hosting=LOCAL)
    else:
        payslip = _provider("gemini", settings.payslip_provider_name, settings.payslip_provider_hosting or
                            settings.ai_provider_hosting)
    return ProcessingPolicy(chat=chat, payslip=payslip, real_data_approved=settings.real_data_approved,
                            allow_full_file_setting=settings.payslip_allow_full_file,
                            require_ai_consent_setting=settings.require_ai_consent)
