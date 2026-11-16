"""Verified facts about data the calculation still needs (16/11).

"איך מחשבים בלי ההשתתפות?" got a general "how I work" answer: no verified source said what can and cannot be calculated
without a value, so the model's correct explanation was cut by the claim verifier, or the model filled the gap itself.
These facts come from the conversation's state (what is missing or unknown now) and from how the engine uses each value:

- today's car (its copay, its use value, the maintenance received without a car): the engine uses them for today's net
  only, so they decide the change in net; a new car's net does not depend on them (the engine computes it, and marks the
  result incomplete without them);
- the salary values: every net, today's and each car's;
- the group / role and the children: which cars are open (the family track), before any calculation.

The system's own rule is said with them: no net is shown until the values are known, nothing is guessed or set to 0,
and a value the employee knows roughly is welcome and marked as an estimate. Tests check the dependencies against the
engine (tests/test_data_facts_2026_11_16.py), so the text never says more than the engine does.
"""
from __future__ import annotations

from typing import Sequence

from app.modules.rules.public import RulesSnapshot

from .guards import FIELD_WORDS, STALE_WORDS
from .stage import ConversationState, current_unknown, missing_current_car, open_profile, unknown_missing

TODAY_ONLY = ("copay", "use_value", "maintenance", "drive", "car", "car_choice")
EVERY_NET = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points")
ELIGIBILITY = ("group", "occupation", "children")

NAMES = {"base": "שכר הבסיס", "commissions": "העמלות", "bonus": "הבונוס", "pension_percent": "אחוז הפנסיה",
         "study_fund_percent": "אחוז קרן ההשתלמות", "credit_points": "נקודות הזיכוי", "children": "מספר הילדים",
         "group": "קבוצת הזכאות", "occupation": "התפקיד", "copay": "ההשתתפות ברכב של היום",
         "use_value": "שווי השימוש של הרכב של היום", "maintenance": "אחזקת הרכב שאתם מקבלים היום",
         "drive": "סוג ההנעה של הרכב של היום", "car": "הרכב שלכם היום", "car_choice": "גרסת הרכב שלכם היום"}
# fields never told apart by their words in a question ("רכב" is in every question about cars)
_NOT_BY_WORD = {"car", "car_choice", "drive", "occupation"}


def missing_now(state: ConversationState) -> list[str]:
    """What the calculation still needs, by field, in the order it is asked (unknown values included)."""
    p, c = state.profile, state.current
    out = [*open_profile(p), *missing_current_car(c), *unknown_missing(p), *current_unknown(c)]
    return [f for f in dict.fromkeys(out) if f in NAMES]


def named_missing(state: ConversationState, text: str) -> list[str]:
    """The missing fields a message names by their own words (the fields' vocabulary, as everywhere else)."""
    return [f for f in missing_now(state) if f not in _NOT_BY_WORD
            and any(w in text for w in (STALE_WORDS.get(f) or FIELD_WORDS.get(f, ())))]


def concerned(state: ConversationState, snapshot: RulesSnapshot, text: str, faq_ids: Sequence[str],
              asks: bool) -> list[str]:
    """The missing fields a question is about: the ones it names; or, for a question about the calculation itself (an
    FAQ entry listed in faq.calculation_entries), the values the employee said they do not know — a field simply not
    asked yet is the flow's next question, never a list of everything still to come. [] for anything else."""
    if not asks:
        return []
    unknown = set(state.profile.unknown_fields) | set(current_unknown(state.current))
    # a field named: unless the question is only its own definition ("מה זה נקודות זיכוי?") while it is merely not asked
    # yet — that is the term's explanation; a value said to be unknown always gets the facts
    named = [f for f in named_missing(state, text) if f in unknown or list(faq_ids) != [f]]
    if named:
        return named
    if set(faq_ids) & set(snapshot.faq.calculation_entries):
        return [f for f in missing_now(state) if f in unknown]
    return []


def _effect(field: str) -> str:
    if field in TODAY_ONLY:
        return ("הנתון הזה נכנס רק לחישוב הנטו של היום, ולכן קובע את השינוי בנטו; "
                "הנטו ברכב חדש לא תלוי בו.")
    if field in EVERY_NET:
        return "הנתון הזה נכנס לחישוב הנטו בכל אחת מהאפשרויות, גם של היום וגם של כל רכב חדש."
    return "הנתון הזה קובע אילו רכבים פתוחים לכם, ולכן נדרש לפני החישוב."


def facts(state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str]) -> list[str]:
    """For each field: whether it is missing or said to be unknown, how the engine uses it, where it is on the payslip;
    then the system's rule. [] without fields."""
    if not fields:
        return []
    p, c = state.profile, state.current
    unknown = set(p.unknown_fields) | set(current_unknown(c))
    lines = []
    for f in fields:
        status = "הערך עדיין לא ידוע" if f in unknown else "הערך עדיין חסר"
        lines.append(f"{NAMES[f]}: {status}. {_effect(f)}")
        entry = snapshot.faq.get(f)              # the FAQ entries are named after the fields
        if entry is not None and entry.where:
            lines.append(f"{NAMES[f]}: {entry.where}")
    lines.append("עד שהנתונים האלה ידועים אין חישוב נטו, ואני לא מציב ערך במקומם ולא 0. אפשר לכתוב אותם מהתלוש, "
                 "או הערכה שלכם שתסומן בתוצאות כהערכה.")
    return lines


def facts_block(lines: Sequence[str], for_model: bool = False) -> str:
    """The facts for the claim verifier (every line), or for the model's prompt: without the system's rule line, which
    code says itself after the model's explanation (so it is never said twice). "" without any."""
    if not lines:
        return ""
    shown = list(lines[:-1]) if for_model else list(lines)
    note = ("\n(The system itself then says that no net is shown until these are known, that nothing is guessed or "
            "set to 0, and that an estimate is welcome: do not say it.)" if for_model else "")
    return ("MISSING DATA FACTS (from the conversation's state and the engine; the only source for what can or "
            "cannot be calculated now):\n" + "\n".join(f"- {line}" for line in shown) + note)
