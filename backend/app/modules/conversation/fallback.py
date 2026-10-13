"""מצב גיבוי בלי מודל שפה: שאלה אחת בכל פעם, והתשובה מתורגמת לקריאות לאותם כלים."""
from __future__ import annotations

import re

from app.modules.rules.public import RulesSnapshot, catalog_names

from .llm.port import ToolCall
from .parsing import find_year, first_number, is_no_car, is_none, is_unknown, match_drive, strip_year
from .stage import ConversationState, next_question
from .tools import OPT_OUT_WORDS, available_options

_RANK_RE = re.compile(r"הכי\s*משתלם|הכי\s*טוב|תמליץ|המלצ|דרג", re.IGNORECASE)


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", text.lower())


def _call(name: str, **args) -> list[ToolCall]:
    return [ToolCall(name=name, args=args, id=f"fallback_{name}")]


def _money_or_zero(field: str, text: str) -> list[ToolCall]:
    if is_none(text):
        return _call("save_profile", **{field: 0})
    n = first_number(text)
    return _call("save_profile", **{field: str(n)}) if n is not None else []


def fallback_calls(state: ConversationState, snapshot: RulesSnapshot, text: str) -> list[ToolCall]:
    field = next_question(state)
    t = text.strip()
    if not t or field is None:
        return []
    n = first_number(t)

    if field == "occupation":
        return _call("save_profile", occupation=t)
    if field == "group":
        return _call("save_profile", eligibility_group=t)
    if field in ("commissions", "bonus", "study_fund_percent"):
        return _money_or_zero(field, t)
    if field == "children":
        if is_none(t):
            return _call("save_profile", children_under_23=0)
        return _call("save_profile", children_under_23=int(n)) if n is not None else []
    if field in ("base", "pension_percent", "credit_points"):
        arg = {"base": "base_salary"}.get(field, field)
        return _call("save_profile", **{arg: str(n)}) if n is not None else []
    if field == "car":
        if is_no_car(t):
            return _call("match_current_car", no_car=True)
        return _call("match_current_car", model=strip_year(t), registration_year=find_year(t))
    if field == "car_choice":
        return _call("match_current_car", choice_number=int(n)) if n is not None else []
    if field == "use_value":
        if is_unknown(t):
            return _call("save_profile", current_use_value_unknown=True)
        return _call("save_profile", current_use_value=str(n)) if n is not None else []
    if field == "drive":
        drive = match_drive(t)
        return _call("save_profile", current_drive=drive.value) if drive else []
    if field == "copay":
        if is_unknown(t):
            return _call("save_profile", current_copay_unknown=True)
        return _money_or_zero("current_copay", t)
    if field == "maintenance":
        return _call("save_profile", current_maintenance=str(n)) if n is not None else []
    if field == "options":
        return _option_calls(state, snapshot, t)
    return []


def _option_calls(state: ConversationState, snapshot: RulesSnapshot, text: str) -> list[ToolCall]:
    if _RANK_RE.search(text):
        return _call("rank_options")
    options = available_options(snapshot, state)
    key = _key(text)
    # שמות וכינויים קודם: שם רכב יכול להכיל ספרה (MAZDA 3), אז לא מפרשים אותה כמספר ברשימה
    named: dict[str, str] = {}
    for o in options:
        if o.car is None:
            continue
        hits = [n for n in map(_key, catalog_names(o.car)) if n and n in key]
        if hits:
            named[o.id] = max(hits, key=len)
    # "קורולה" בתוך "קורולה קרוס" — נשאר רק השם הארוך
    chosen = [i for i, n in named.items() if not any(n != m and n in m for m in named.values())]
    if any(w in text.lower() for w in OPT_OUT_WORDS):
        chosen.append("OPT_OUT")
    if not chosen:
        for m in re.findall(r"\d+", text):
            i = int(m)
            if 1 <= i <= len(options):
                chosen.append(options[i - 1].id)
    return _call("calculate", options=chosen) if chosen else []
