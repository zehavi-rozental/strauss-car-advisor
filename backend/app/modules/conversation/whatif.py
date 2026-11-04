"""A real change, or only a check? "אני מרוויח עכשיו 20,000" is a change; "מה יקרה אם ארוויח 20,000?" is a check.

Code decides, in both modes, and in doubt it asks. A check runs on a copy of the state: the same gates, the same eligibility
function and the same engine call as a comparison, and nothing of it is saved. Word lists live in extensions.yaml
(`hypothetical_cues`, `declared_change_cues`, `salary_words`): a new phrasing is a line there.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.modules.calculation.public import evaluate_state
from app.modules.calculation.public import Status as CalcStatus
from app.modules.rules.public import RulesSnapshot, words

from .option_map import evaluate_option
from .fallback import named_cars
from .parsing import parse_copay, read_profile
from .stage import PROFILE_ARGS, ConversationState, map_missing
from .templates import FIELD_NAMES, _VALUE_LABELS, _show, labelled_value, money
from .tools import (
    NoArgs,
    SaveProfileArgs,
    ToolContext,
    ToolError,
    available_options,
    classify_options,
    engine_inputs,
    rank_options,
    save_profile,
)

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_AMOUNT_RE = re.compile(rf"({_NUMBER})\s*(אלף)?")
_GROUP_RE = re.compile(r"קבוצ(?:ה|ת)(?:\s+זכאות)?\s+([א-ד])(?![א-ת])")
_NOT_A_CONDITION = {"אפשר", "ניתן", "כך", "כן", "לא"}             # "אם אפשר, תעדכן": a request, not a condition
_UNSURE_BEFORE = {"יודע", "יודעת", "יודעים", "בטוח", "בטוחה", "בטוחים", "זוכר", "זוכרת", "זוכרים"}   # "לא יודע אם": not a condition either
_NAMES_A_VALUE = re.compile(r"\d[\d,]{2,}|\d+\s*אלף|\d\s*%")       # an amount or a percentage; "MAZDA 3" is a car


@dataclass(frozen=True)
class Change:
    kind: str                                   # what_if / update / ambiguous
    values: dict[str, Decimal] = field(default_factory=dict)   # save_profile arguments
    group: Optional[str] = None
    cars: tuple[str, ...] = ()                  # catalog codes named in a check


def says_any(text: str, cues: Any) -> bool:
    """A cue as whole words, in order; a one-letter prefix (ו, ה, ב, ל, ש) is allowed on its first word."""
    if not cues:
        return False
    for cue in cues:
        pattern = r"(?<![א-ת\w])[והבלשמ]?" + r"\s+".join(map(re.escape, cue.split())) + r"(?![א-ת\w])"
        if re.search(pattern, text):
            return True
    return False


def is_hypothetical(text: str, snapshot: RulesSnapshot) -> bool:
    """The message asks about a value that is not (yet) true: "מה יקרה אם", "נניח", "תרחיש", or a condition anywhere in it
    ("כמה יישאר לי אם אקבל 20,000?"). A mistake here is the safe one: nothing is saved, and the check offers to save."""
    ext = snapshot.extensions
    if says_any(text, ext.hypothetical_cues):
        return True
    if _NAMES_A_VALUE.search(text) and says_any(text, ext.hypothetical_value_cues):
        return True
    ws = words(text)
    for i, w in enumerate(ws):
        if w in ("אם", "ואם") and (i + 1 == len(ws) or ws[i + 1] not in _NOT_A_CONDITION) and (i == 0 or ws[i - 1] not in _UNSURE_BEFORE):
            return True
    return False


def _amount(raw: str, thousands: Optional[str]) -> Optional[Decimal]:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    return value * 1000 if thousands else value


def _salary_value(text: str, snapshot: RulesSnapshot) -> Optional[Decimal]:
    """A salary said with a verb or a word the profile reader does not read: "ארוויח 20,000", "השכר עלה ל-20 אלף"."""
    terms = "|".join(map(re.escape, snapshot.extensions.salary_words))
    if not terms:
        return None
    m = re.search(rf"(?<![א-ת\w])[והבלשמ]?(?:{terms})(?![א-ת\w])[^\d]{{0,30}}?({_NUMBER})\s*(אלף)?", text)
    if m is None:       # the amount first: "20,000 שכר", "תקן ל-20,000 את השכר". Not a small number: "3 ילדים והשכר" names no salary
        m = re.search(rf"(\d[\d,]{{2,}}|\d+(?=\s*אלף))\s*(אלף)?[^\d]{{0,12}}?(?<![א-ת\w])[והבלשמ]?(?:{terms})(?![א-ת\w])", text)
    return _amount(m.group(1), m.group(2)) if m else None


_PERCENT_FIRST = {"pension_percent": r"פנסיה", "study_fund_percent": r"קרן\s+(?:ה)?השתלמות|השתלמות"}


def _percent_first(text: str) -> dict[str, Decimal]:
    """"7% לפנסיה": the percentage before its word, which the profile reader reads the other way round."""
    out = {}
    for arg, word in _PERCENT_FIRST.items():
        m = re.search(rf"(\d+(?:\.\d+)?)\s*%\s*(?:של\s+)?[לבו]?(?:{word})", text)
        if m:
            out[arg] = Decimal(m.group(1))
    return out


def _lone_salary(snapshot: RulesSnapshot, text: str) -> Optional[Decimal]:
    """One amount in a hypothetical, with no word for what it is, in the range of a salary: "נגיד שאני מקבל 20,000". The check says
    which value it took, and nothing is saved."""
    bounds = snapshot.extensions.value_ranges.get("base_salary")
    amounts = [v for m in re.finditer(rf"(\d[\d,]{{2,}}|\d+(?=\s*אלף))\s*(אלף)?", text) if (v := _amount(m.group(1), m.group(2))) is not None]
    return amounts[0] if len(amounts) == 1 and bounds is not None and max(bounds[0], Decimal(3000)) <= amounts[0] <= bounds[1] else None


def read_values(state: ConversationState, snapshot: RulesSnapshot, text: str) -> dict[str, Decimal]:
    """The values a message names, labelled: the profile reader's forms, a salary said with a verb, a copay."""
    ext = snapshot.extensions
    reading = read_profile(text, snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
    values = {k: Decimal(str(v)) for k, v in reading.fields.items() if k in PROFILE_ARGS}
    if "base_salary" not in values and (salary := _salary_value(text, snapshot)) is not None:
        values["base_salary"] = salary
    for arg, value in _percent_first(text).items():
        values.setdefault(arg, value)
    if "השתתפות" in text and state.current.source in ("catalog", "tax", "stated"):
        copay = parse_copay(text)
        if copay is not None:
            values["current_copay"] = copay
    return values


def _current(state: ConversationState, arg: str) -> Any:
    if arg == "current_copay":
        return state.current.copay
    return getattr(state.profile, PROFILE_ARGS[arg]) if arg in PROFILE_ARGS else None


def read_change(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[Change]:
    """None: not about changing a saved value. The hypothetical wins over everything; a declared change of a value that
    is already saved is an update; the same value with a question mark is asked back."""
    hypothetical = is_hypothetical(text, snapshot)
    values = read_values(state, snapshot, text)
    if hypothetical:
        group = _GROUP_RE.search(text)
        cars = tuple(c.catalog_code for c in named_cars(text, snapshot.catalog))
        if not values and not group and not cars and (lone := _lone_salary(snapshot, text)) is not None:
            values = {"base_salary": lone}
        if values or group or cars:
            return Change("what_if", values, group.group(1) if group else None, cars)
        return None
    if not values and (salary := _instead_of_saved(state, text)) is not None:
        return Change("update", {"base_salary": salary})            # "לא 18,000, אלא 20,000": the saved salary is named, so it is it
    replaced = {k: v for k, v in values.items() if _current(state, k) is not None
                and Decimal(str(_current(state, k))) != v}
    if not replaced or set(replaced) != set(values):
        return None
    if "?" in text:
        return Change("ambiguous", replaced)
    if says_any(text, snapshot.extensions.declared_change_cues):
        return Change("update", replaced)
    return None


def _instead_of_saved(state: ConversationState, text: str) -> Optional[Decimal]:
    """Two amounts, one of them the saved salary, with a word that replaces one by the other."""
    base = state.profile.base
    if base is None or not re.search(r"(?<![א-ת])(?:אלא|ולא|במקום)(?![א-ת])", text):
        return None
    amounts = [v for m in _AMOUNT_RE.finditer(text) if (v := _amount(m.group(1), m.group(2))) is not None]
    if len(amounts) != 2 or base not in amounts or amounts[0] == amounts[1]:
        return None
    return amounts[0] if amounts[1] == base else amounts[1]


def read_loose_change(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[dict[str, Decimal]]:
    """A value that differs from the saved one, said with nothing that tells a change from a question ("שכר בסס 20000"): asked,
    never guessed and never left as "not understood"."""
    values = read_values(state, snapshot, text)
    replaced = {k: v for k, v in values.items() if _current(state, k) is not None and Decimal(str(_current(state, k))) != v}
    return replaced if replaced and set(replaced) == set(values) else None


def read_bare_amount(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[dict[str, Decimal]]:
    """A bare amount in the range of a monthly salary, when no question is open and the salary is known ("20,000" after
    the results): nothing says what it is, so it is asked. A number that answers a question never comes here."""
    m = _AMOUNT_RE.fullmatch(text.strip().rstrip("?").strip().removesuffix("₪").strip())
    if m is None or state.profile.base is None:
        return None
    value = _amount(m.group(1), m.group(2))
    bounds = snapshot.extensions.value_ranges.get("base_salary")
    if value is None or bounds is None or not bounds[0] <= value <= bounds[1] or value == state.profile.base:
        return None
    return {"base_salary": value}


# ---------- the check itself: on a copy, never saved ----------

@dataclass
class ScenarioOutcome:
    card: Optional[dict[str, Any]] = None       # kind "scenario": what the employee sees and what its button applies
    problem: Optional[str] = None               # why it was not calculated, in Hebrew
    missing: list[str] = field(default_factory=list)


def _figures(snapshot: RulesSnapshot, state: ConversationState, ids: list[str]) -> tuple[Any, dict[str, dict[str, Any]]]:
    employee, current, occupation = engine_inputs(snapshot, state)
    today = evaluate_state(employee, current, snapshot.calc)
    options = {o.id: o for o in available_options(snapshot, state)}
    out: dict[str, dict[str, Any]] = {}
    for option_id in ids:
        option = options.get(option_id)
        if option is None:
            continue
        _, result = evaluate_option(snapshot, state, option, employee, current, occupation)
        ok = result.status == CalcStatus.OK and result.delta_display is not None
        out[option_id] = {"label": option.label, "ok": ok,
                          "net": str(result.alternative.net_display) if ok else None,
                          "delta": str(result.delta_display) if ok else None}
    return (str(today.net_display) if today is not None else None), out


def _value_rows(state: ConversationState, values: dict[str, Decimal]) -> list[dict[str, str]]:
    rows = []
    for arg, value in values.items():
        current = _current(state, arg)
        rows.append({"field": arg, "label": _VALUE_LABELS[arg][0], "after": _show(arg, value),
                     "before": _show(arg, current) if current is not None else ""})
    return rows


def run_scenario(snapshot: RulesSnapshot, state: ConversationState, change: Change) -> ScenarioOutcome:
    """The values on a deep copy of the state, through save_profile's own gates; the same selection (ranked again if it came
    from the ranking) and any car the check names, through the engine. `state` itself is never touched."""
    missing = map_missing(state)
    if missing:
        return ScenarioOutcome(missing=[FIELD_NAMES.get(f, f) for f in missing])
    trial = state.model_copy(deep=True)
    trial.pending_confirmation = None
    trial.confirmed_unusual = list(dict.fromkeys([*trial.confirmed_unusual, *change.values]))
    ctx = ToolContext(snapshot=snapshot, find_vehicle=lambda *_: None, state=trial, exact_amounts=True)
    if change.values:
        save_profile(ctx, SaveProfileArgs(**{k: str(v) for k, v in change.values.items()}))
        if ctx.range_problems:
            arg, value, _ = ctx.range_problems[0]
            return ScenarioOutcome(problem=f"אי אפשר לבדוק {_VALUE_LABELS[arg][0]} {_show(arg, value)}: "
                                           "הערך מחוץ לטווח שהמערכת מאפשרת.")
    rows = _value_rows(state, change.values)
    if change.group and change.group != state.profile.group:
        trial.profile.group = change.group
        rows.append({"field": "group", "label": "קבוצת זכאות", "after": f"קבוצה {change.group}",
                     "before": f"קבוצה {state.profile.group}" if state.profile.group else ""})
    if state.ranked_count:
        try:
            rank_options(ctx, NoArgs())
        except ToolError:
            trial.selected = []
    cars = [c for c in change.cars if c != state.current.catalog_code]
    eligible = {o.id for o in available_options(snapshot, trial)}
    excluded = {x.label: x.reason for x in classify_options(snapshot, trial)[1]}
    labels = {c.catalog_code: c.label for c in snapshot.catalog}
    not_eligible = [{"option_id": c, "label": labels.get(c, c), "reason": excluded.get(labels.get(c, c), "לא זמין לכם")}
                    for c in cars if c not in eligible]
    before_ids = list(state.selected)
    after_ids = list(dict.fromkeys([*[o for o in trial.selected if o in eligible], *[c for c in cars if c in eligible]]))
    if change.cars and not cars and not rows:
        return ScenarioOutcome(problem=f"{labels.get(change.cars[0], '')} הוא הרכב שלכם היום, ולכן אין מה לבדוק מולו.")
    today_before, base = _figures(snapshot, state, before_ids)
    today_after, trial_figures = _figures(snapshot, trial, after_ids)
    rank = lambda figures: sorted((i for i, f in figures.items() if f["ok"]),   # noqa: E731
                                  key=lambda i: Decimal(figures[i]["delta"]), reverse=True)
    options = [{"option_id": i, "label": f["label"], "after": {"net": f["net"], "delta": f["delta"]},
                "before": ({"net": base[i]["net"], "delta": base[i]["delta"]} if i in base and base[i]["ok"] else None)}
               for i, f in sorted(trial_figures.items(), key=lambda kv: -Decimal(kv[1]["delta"] or "-999999"))
               if f["ok"]]
    left = [{"option_id": i, "label": base[i]["label"],
             "reason": excluded.get(base[i]["label"], "לא זמין לכם בבדיקה הזו")}
            for i in before_ids if i in base and i not in trial_figures]
    apply: dict[str, Any] = {}
    if change.values and not (change.group and state.profile.occupation):
        apply["values"] = {k: str(v) for k, v in change.values.items()}
    if change.group and not state.profile.occupation and change.group != state.profile.group:
        apply["group"] = change.group
    if not change.values and not change.group and len(state.selected) < 3:
        addable = [c for c in cars if c in eligible and c not in state.selected]
        if addable:
            apply["add_options"] = addable[:3 - len(state.selected)]
    card = {"kind": "scenario", "based_on": state.data_version, "values": rows,
            "today_net": {"before": today_before, "after": today_after}, "options": options, "left": left,
            "not_eligible": not_eligible, "order_changed": rank(base)[:len(options)] != rank(trial_figures)[:len(base)]
            if base and options else False, "apply": apply}
    return ScenarioOutcome(card=card)


def _delta_words(delta: Any) -> str:
    d = Decimal(str(delta))
    return f"תוספת של {money(d)}" if d > 0 else f"{money(-d)} פחות" if d < 0 else "בלי שינוי"


def scenario_text(card: dict[str, Any]) -> str:
    """The chat line of a check: it says first that nothing changed; the card holds the detail."""
    lines = ["בדיקה בלבד: הנתונים שלכם לא השתנו."]
    head = ", ".join(f"{r['label']} {r['after']}" for r in card["values"])
    today = card["today_net"]
    if head and today["before"] != today["after"]:
        lines.append(f"עם {head}, הנטו שלכם ברכב של היום יהיה {money(Decimal(today['after']))} "
                     f"במקום {money(Decimal(today['before']))}.")
    elif head:
        lines.append(f"עם {head}, הנטו שלכם ברכב של היום לא ישתנה.")
    for item in card["options"][:3]:
        before = item["before"]
        tail = f" במקום {_delta_words(before['delta'])}" if before and before["delta"] != item["after"]["delta"] else ""
        lines.append(f"{item['label']}: {_delta_words(item['after']['delta'])} בחודש{tail}.")
    for item in [*card["left"], *card["not_eligible"]]:
        lines.append(f"{item['label']}: {item['reason']}.")
    return "\n".join(lines)


SCENARIO_ASK_VALUES = "לעדכן את זה כנתון שלכם?"
SCENARIO_ASK_ADD = "להוסיף להשוואה?"


def scenario_ask(card: dict[str, Any]) -> str:
    apply = card.get("apply") or {}
    if apply.get("values") or apply.get("group"):
        return SCENARIO_ASK_VALUES
    return SCENARIO_ASK_ADD if apply.get("add_options") else ""


def intent_question(values: dict[str, Decimal]) -> tuple[str, str]:
    """(the label of the value, the question): "לעדכן את שכר בסיס ל-20,000 ₪, או רק לבדוק מה יקרה?" """
    label = ", ".join(labelled_value(k, v) for k, v in values.items())
    return label, f"לעדכן את הנתון שלכם ל{label}, או רק לבדוק מה יקרה?"
