"""faq.yaml: grounded answers to the employee's questions.

Numbers are never written in the file: {placeholders} are filled from strauss-rules.json, so the
answers follow the rules. A wrong schema or an unknown placeholder fails loading with a Hebrew error.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

FAQ_FILE = "faq.yaml"


class FaqEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    term: str                                  # words a reply that answers this entry must contain
    aliases: tuple[str, ...] = ()
    answer: str
    simple: Optional[str] = None               # the question again, in simpler words
    example: Optional[str] = None              # what to type
    where: Optional[str] = None                # where to find the value on the payslip


class Faq(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = ""
    entries: tuple[FaqEntry, ...] = ()
    redirect: str = ("אני כאן כדי לעזור לכם להבין איך בחירת רכב החברה משפיעה על הנטו החודשי שלכם, ולהשוות בין "
                     "האפשרויות לפי הסדרי שטראוס. אפשר לשאול אותי על רכבי החברה, הזכאות וההשפעה על השכר.")
    injection_patterns: tuple[str, ...] = ()
    off_topic_patterns: tuple[str, ...] = ()
    # what the service is about: a question no entry answers goes to the representative only when it names one
    # of these (or an FAQ topic, or a catalog car); anything else gets the redirect line
    domain_terms: tuple[str, ...] = ()
    # words that refer back to the conversation ("ומה זה?", "למה?", "ואם…"): a follow-up, never off topic
    context_markers: tuple[str, ...] = ()
    # plainly not about the service (whole words); a question naming one, and nothing of the service, is redirected
    off_topic_terms: tuple[str, ...] = ()
    # the employee's own pay / net ("אני מרוויחה יותר?"): answered by the calculation, never sent to the rep
    net_intent_terms: tuple[str, ...] = ()
    # P0-A (22/10): words any car, tax or pay question has ("רכב", "מס", "שכר"): never enough alone
    generic_domain_terms: tuple[str, ...] = ()
    # "הרכב" (the car being discussed): concrete unless the question points back ("ומה עם הרכב השני?")
    reference_terms: tuple[str, ...] = ()
    # another domain ("קניית דירה", "יד שנייה", "להשכרה"): the redirect line unless a company anchor is named
    foreign_context_terms: tuple[str, ...] = ()
    # plainly the company's arrangement ("רכב צמוד", "רכב החברה"), whatever else the question names
    company_anchor_terms: tuple[str, ...] = ()
    # entries about the conversation itself (the representative, privacy, starting over): an answer, never a
    # reason to send a question to the representative
    meta_entries: tuple[str, ...] = ()
    # the employee's own data (rule A, 22/10): a question that names only these ("חייבים לכתוב עמלות?", "למה אתה
    # שואל על ילדים?") is about why the bot asks, answered here — the representative never knows the employee's pay
    employee_data_terms: tuple[str, ...] = ()
    # what a result on screen is made of ("הערכה זמנית", "ממתינה לאישור", "השתתפות"): with results shown, a question
    # naming one is about those results and is answered from them (C5, 22/10), never sent to the representative
    result_terms: tuple[str, ...] = ()
    # a reason to ask the representative about a new car's copay, said by the employee with the copay on screen
    # ("שהנציג יאשר", "סוכם אחרת", "לברר"): with no amount given, a question is opened for that car
    copay_rep_terms: tuple[str, ...] = ()
    # a question whose intent is truly unclear: one short line, no buttons, never the representative
    clarify: str = "לא בטוח שהבנתי את השאלה. כתבו אותה במילים אחרות, למשל על הרכב, הזכאות או הנטו."

    def get(self, entry_id: str) -> Optional[FaqEntry]:
        return next((e for e in self.entries if e.id == entry_id), None)

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.entries]


def _plain(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return f"{int(value):,}" if value == int(value) and abs(value) >= 1000 else text


def faq_values(strauss: Any, max_options: int) -> dict[str, str]:
    ft = strauss.family_track
    return {
        "electric_net_bonus": _plain(Decimal(str(strauss.electric.net_bonus))),
        "credit_point_value": _plain(Decimal(str(strauss.net_method.credit_point_value))),
        "family_min_children": str(ft.min_children),
        "child_max_age": str(ft.child_max_age),
        "family_gap_percent": _plain(Decimal(str(ft.gap_split_employee)) * 100),
        "groups": "-".join([strauss.group_order[0], strauss.group_order[-1]]),
        "max_options": str(max_options),
    }


def load_faq(config_dir: Path, values: dict[str, str]) -> Faq:
    """Missing file → an empty FAQ (every question goes to the representative)."""
    from .loader import ConfigError, _schema_error

    path = config_dir / FAQ_FILE
    if not path.exists():
        return Faq()
    try:
        faq = Faq.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise _schema_error(FAQ_FILE, exc) from exc
    if len(set(faq.ids)) != len(faq.ids):
        raise ConfigError(f"{FAQ_FILE}: מזהה כפול ברשימת השאלות")
    entries = []
    for e in faq.entries:
        try:
            filled = {k: (getattr(e, k).format(**values) if getattr(e, k) else getattr(e, k))
                      for k in ("answer", "simple", "example", "where")}
        except KeyError as exc:
            raise ConfigError(f"{FAQ_FILE}: ערך לא מוכר {{{exc.args[0]}}} בשאלה {e.id}") from exc
        entries.append(e.model_copy(update=filled))
    return faq.model_copy(update={"entries": tuple(entries)})


__all__ = ["FAQ_FILE", "Faq", "FaqEntry", "faq_values", "load_faq"]
