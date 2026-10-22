"""תור אחד בשיחה: UNDERSTAND → DECIDE → SAY.

LLM-led (turn.py): one tool-calling call reads the message, saves through tools (every call checked in
code), answers and asks; guards.py checks the reply (numbers, sources, claims, progress, shape), with one
retry, then the reply without the offending sentences, then the template. Code still owns the state
machine, the calculation, the rep flow and the template path (no LLM / outage / results / rep messages).
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from app.modules.calculation.public import CopayStatus, ScenarioResult, evaluate
from app.modules.calculation.public import Status as CalcStatus
from app.modules.cases.public import AnswerType, MissingField, Mode, QuestionAnswered, QuestionRequest
from app.modules.rules.public import (FAMILY_GROUP, CatalogCar, RulesSnapshot, find_catalog_car, matched_name,
                                      related_cars, words)
from app.modules.rules.public import catalog_names as catalog_names_of


from .expects import build_expects, choice_calls
from .fallback import (ZERO_ALLOWED, ParserPlan, _profile_value_correction_plan, asks_base, car_roles, correction_plan,
                       fallback_plan, named_cars, none_plan, recorded_text)
from .free_text import looks_like_title
from .guard import collect_numbers, violations
from .llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from .grounding import (grounded_number, group_named, occupation_named, option_named, said_numbers,
                        without_alternatives, zero_grounded)
from .parsing import (YEAR_RE, has_all_scope, mask_ages, match_drive, match_group, match_occupation, no_car_span,
                      occupation_match, parse_copay, read_profile, redact_id_numbers)
from .guards import (FIELD_WORDS, SINGULAR_RE, TAG_RE, Check, Violation, ask_lines, check, content_sentences, drop,
                     has_statement, sentences, strip_tags, verify)
from .turn import LLM_TOOLS, Candidate, parser_hint, render_catalog, render_faq, run_rounds, state_block, system_message, user_message
from .option_map import build_option_map, dump_option, dump_result as _dump, evaluate_option, map_reply
from .option_map import with_assumptions as _with_assumptions
from .understanding import NO_CAR_OR_ZERO, NOT_IN_CATALOG, Understanding, rep_scope_ok, says_phrase, understand
from .stage import (
    allowed_tools,
    current_group,
    current_unknown,
    MAX_OPTIONS,
    CarClarification,
    ConfirmedCopay,
    ConversationState,
    GeneralQuestion,
    OptionResult,
    PendingRep,
    RepReason,
    Stage,
    blocking_rep_reason,
    map_ready,
    missing_current_car,
    missing_profile,
    open_profile,
    next_stage,
    results_stale,
    ARG_OF,
    PROFILE_ARGS,
    profile_args,
    Q2_FIELDS,
)
from .templates import (
    ALL_AT_ONCE,
    NEXT_AFTER_RESULTS,
    UNANSWERED,
    compare_needs_text,
    unknown_ack_text,
    call_to_action,
    money,
    plan_reply,
    GREETING,
    RANK_DEFERRED,
    REP_DEFERRED,
    chosen_options_text,
    confirm_group_text,
    clarify_car_text,
    confirmed_assumption,
    paced,
    conflict_text,
    queued_option_text,
    range_problem_text,
    range_refused_text,
    render,
    rep_changed_text,
    rep_confirmed_text,
    rep_current_text,
    rep_maintenance_text,
    rep_note_text,
    rep_copay_text,
    rep_group_text,
    copay_rep_request_text,
    results_explain_text,
    stated_copay_text,
    group_conflict_text,
    form_saved_text,
    rep_opt_out_text,
    results_text,
    unusual_fix_text,
)
from .tools import (
    OPT_OUT_ID,
    OPT_OUT_WORDS,
    TOOL_SPECS,
    ToolContext,
    VehicleGetter,
    ToolError,
    VehicleLookup,
    available_options,
    engine_inputs,
    invalidate_results,
    option_state,
    rep_car_label,
    safe_car_label,
    resolve_option,
    run_tool,
)

log = logging.getLogger(__name__)

HISTORY = 12

REP_QUESTION_PROMPT = """Rewrite the employee's question below as one short, neutral Hebrew question for the company's
transport representative. Remove every salary, amount, percentage and personal number, and anything
personal; keep only what the question is about (a car model and its year may stay).
Return only the question."""


RecordRun = Callable[[str, dict[str, Any], ScenarioResult], str]


@dataclass
class TurnOutcome:
    reply: str
    source: str               # llm / template
    guard_blocked: bool = False
    just_calculated: bool = False
    path: str = "parser"      # parser / llm / fallback / templates / waiting — ללוג, בלי תוכן
    # SAY: "llm", or why the template was used: deadline / outage / empty / guard / coverage:<what> /
    # not_tried (results, rep messages, buttons, no LLM)
    say: str = "not_tried"
    # לשלב הכתיבה של התור: שאלות לפתוח ושאלות לבטל, באותה טרנזקציה
    question_requests: list[QuestionRequest] = field(default_factory=list)
    cancel_question_ids: list[str] = field(default_factory=list)
    # a calculation ran for this selection: open new-car copay questions for other cars are cancelled (a car taken
    # out of the comparison); answered ones and their amounts are kept
    copay_selection: Optional[list[str]] = None


CUE_WORDS = 3                  # מילת תיקון נספרת באחת משלוש המילים הראשונות
_CUE_PREFIXES = "והבלש"         # "ובעצם", "שטעיתי"

WAITING_CORRECTION_PROMPT = """The employee is waiting for the transport representative to answer a question about
their CURRENT company car ({car}). Their new message mentions a car.
Call match_current_car (model name in Latin letters, e.g. "COROLLA CROSS") ONLY if the message corrects which car
they have today. If they ask about a car as an alternative, or you are not sure, do not call any tool."""


def _has_cue(text: str, cues: list[str]) -> bool:
    cue_set = {w for c in cues for w in words(c)}
    return any(w in cue_set or (len(w) > 1 and w[0] in _CUE_PREFIXES and w[1:] in cue_set)
               for w in words(text)[:CUE_WORDS])


def _clarification_answer(text: str) -> Optional[str]:
    """תשובה לשאלה "הרכב שלכם או חלופה?": mine / alternative / None (משהו אחר)."""
    ws = words(text)
    if not ws or len(ws) > 4:
        return None
    if ws in (["כן"], ["נכון"]) or ("הרכב" in ws and "שלי" in ws and "לא" not in ws):
        return "mine"
    if ws == ["לא"] or "חלופה" in ws:
        return "alternative"
    return None


_YES = {"כן", "נכון", "בדיוק", "אכן", "yes", "לאשר", "אשר", "בטח", "ברור", "כמובן"}
_NO = {"לא", "no", "לתקן", "תקן"}


def _confirmation_plan(state: ConversationState, text: str,
                       snapshot: Optional[RulesSnapshot] = None) -> Optional[tuple[ParserPlan, str]]:
    """תשובה ל"התכוונתם ל-18,000 ₪?": the answer is the message's first word ("כן" / "לא" / a button id) or the
    amount itself, never only a short message (live QA 22/10: "כן, אבל היית צריך לשאול אותי קודם" dropped the
    held salary). Returns the plan and the rest of the message, which is read as usual; None when the message
    does not answer the question (the value stays held and is asked again)."""
    ws = words(text)
    pending = state.pending_confirmation
    if not ws or pending is None:
        return None
    stated = without_alternatives(text)              # "45 או 45 אלף?" states neither amount
    same = (pending.value, pending.value * 1000, pending.value / 1000, Decimal(1000))   # the held amount, said any way
    # a number labelled with another field ("כן, ופנסיה 6%") is that field's value, not another amount for this one
    labelled: set[Decimal] = set()
    if snapshot is not None:
        ext = snapshot.extensions
        for key, value in read_profile(text, (), ext.correction_cues, ext.value_qualifiers).labelled.items():
            if key != pending.field and isinstance(value, (int, float, Decimal, str)) and re.fullmatch(
                    r"\d+(?:\.\d+)?", str(value)):
                labelled.add(Decimal(str(value)))
    others = {n for n in said_numbers(stated) if n not in same and n not in labelled}
    rest = text.split(maxsplit=1)[1].lstrip(" ,.-") if len(ws) > 1 and ws[0] in (*_YES, *_NO) else ""

    def save(amount: Decimal, how: str) -> ParserPlan:
        state.pending_confirmation = None
        if (pending.unusual or pending.scale) and pending.field not in state.confirmed_unusual:
            state.confirmed_unusual.append(pending.field)   # the chosen amount is saved, not asked again
        args = {pending.field: str(amount), "approximate_fields": [pending.field] if pending.approximate else []}
        return ParserPlan([ToolCall("save_profile", args, how)], True, direct=True)

    if pending.scale:
        # "3 ₪ או 3,000 ₪?": the button, or the amount said ("3,000", "שלושת אלפים", "כן, 3 אלף")
        amount = {"small": pending.value, "thousands": pending.value * 1000}.get(ws[0])
        if amount is None and not others:
            if grounded_number(pending.value * 1000, stated):
                amount = pending.value * 1000
            elif pending.value in said_numbers(stated):
                amount = pending.value
        return None if amount is None else (save(amount, "scale"), "")
    if ws[0] in _YES and not others:
        return save(pending.value, "confirmed"), rest
    if ws[0] in _NO:
        state.pending_confirmation = None
        if others:
            return None                              # "לא, 50 אלף": a new value, read as usual
        if pending.unusual:
            # "לתקן": the same field is asked again, and it is the only question of the reply
            return ParserPlan(complete=True, notice=unusual_fix_text(pending.field), direct=True,
                              asks=pending.field), rest
        return ParserPlan(complete=True, direct=True), rest
    if not others and "?" not in text and grounded_number(pending.value, stated) and not pending.unusual:
        return save(pending.value, "confirmed"), ""  # the amount itself: "45 אלף", "ארבעים וחמש אלף" ("זה 45 אלף?" asks)
    return None


# ---------- בדיקה צולבת: מודל השפה מול מה שהקורא קרא במלואו ----------

def _expected_values(snapshot: RulesSnapshot, plan: ParserPlan) -> dict[str, Any]:
    """מה הקורא קרא (רק כשקרא במלואו): ערכי save_profile, הרכב הנוכחי, החלופות."""
    expected: dict[str, Any] = {}
    for call in plan.calls:
        if call.name == "save_profile":
            expected.update({f"profile:{k}": v for k, v in call.args.items()
                             if k != "approximate_fields" and v is not None})
        elif call.name == "match_current_car" and call.args.get("model"):
            cars = find_catalog_car(snapshot, call.args["model"])
            if cars:
                expected["car"] = cars[0].catalog_code
        elif call.name == "calculate":
            expected["options"] = sorted(call.args.get("options", []))
    return expected


def _same(a: Any, b: Any) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:  # noqa: BLE001 — לא מספר
        return str(a).strip() == str(b).strip()


def _cross_check(ctx: ToolContext, call: ToolCall, expected: dict[str, Any], conflicts: list[str]) -> ToolCall:
    """ערך של המודל שסותר ערך שהקורא קרא במלואו לאותו שדה — לא נשמר אף אחד מהם, ושואלים."""
    if not expected:
        return call
    snapshot = ctx.snapshot
    if call.name == "save_profile":
        args = dict(call.args)
        for key, value in call.args.items():
            mine = expected.get(f"profile:{key}")
            if mine is None or value is None or key == "approximate_fields":
                continue
            if key == "occupation":
                agree = match_occupation(snapshot.occupations.keys(), str(value)) == mine
            else:
                agree = _same(value, mine)
            if not agree:
                args.pop(key)
                conflicts.append(key)
        return ToolCall(call.name, args, call.id)
    if call.name == "match_current_car" and "car" in expected and call.args.get("model"):
        cars = find_catalog_car(snapshot, call.args["model"])
        if not cars or cars[0].catalog_code != expected["car"]:
            conflicts.append("current_car")
            return ToolCall(call.name, {}, call.id)          # ריק — לא מתאים דבר
    if call.name in ("calculate", "select_options") and "options" in expected:
        # שמות שהמודל שלח → מזהי חלופות, כמו שהכלי עצמו מפרש אותם
        options = available_options(snapshot, ctx.state)
        resolved = [resolve_option(snapshot, options, str(t)) for t in call.args.get("options", [])]
        if None in resolved or sorted({o.id for o in resolved if o}) != sorted(set(expected["options"])):
            conflicts.append("options")
            return ToolCall(call.name, {"options": []}, call.id)
    return call


def _mark_asked(state: ConversationState) -> None:
    """השאלה המקובצת שמוצגת עכשיו נרשמת כ"נשאלה" (Q2 לא נשאלת פעמיים אחרי תשובה חלקית)."""
    group = current_group(state)[0]
    if group in ("Q1", "Q2", "Q3") and group not in state.asked_groups:
        state.asked_groups.append(group)


def _occupation_answer(state: ConversationState, text: str) -> Optional[str]:
    """תשובה ל"לא מצאתי את ... בטבלה": "yes", שם הצעה (לפי מספרה), "no", או None (תשובה אחרת — נקראת כרגיל)."""
    ws = words(text)
    pending = state.pending_occupation
    if not ws or len(ws) > 5 or pending is None:
        return None
    if ws[0] in _YES:
        return "yes"
    if len(ws) == 1 and ws[0].isdigit() and 1 <= int(ws[0]) <= len(pending.suggestions):
        return pending.suggestions[int(ws[0]) - 1]
    if ws == ["לא"]:
        return "no"
    return None


def _occupation_plan(ctx: ToolContext, answer: str) -> ParserPlan:
    """yes → נשמר כתפקיד שאינו בטבלה (קבוצת הזכאות נשאלת אחר כך); הצעה → התפקיד מהטבלה וקבוצתו;
    no → שואלים שוב על התפקיד."""
    pending = ctx.state.pending_occupation
    ctx.state.pending_occupation = None
    if answer == "no" or pending is None:
        return ParserPlan(complete=True, direct=True)
    if answer == "yes":
        ctx.allow_unknown_occupation = True
        return ParserPlan([ToolCall("save_profile", {"occupation": pending.text}, "occupation_confirmed")],
                          True, direct=True)
    return ParserPlan([ToolCall("save_profile", {"occupation": answer}, "occupation_suggested")], True, direct=True)


def _plan_notice(plan: ParserPlan, path: str) -> Optional[str]:
    """משפט מהקורא: כשפעל (parser), או כשהקריאות שלו רצו כגיבוי (fallback / templates)."""
    if path in ("parser", "choice"):
        return plan.notice
    if path in ("fallback", "templates"):
        return plan.fallback_notice
    return None


def _car_key(state: ConversationState) -> tuple:
    c = state.current
    return (c.source, c.catalog_code, c.label, c.use_value)


def _recorded(facts: dict[str, Any]) -> bool:
    """הכלי רץ ושמר משהו. קריאה כפויה בלי שדות (save_profile ריק) אינה "הבנה"."""
    return bool(facts.get("ok")) and facts.get("saved") != []


def _role_conflict(ctx: ToolContext, call: ToolCall, text: str) -> Optional[CatalogCar]:
    """המודל קרא ל-match_current_car על רכב שנכתב כחלופה ("רוצה לבדוק"), או ל-calculate על רכב שנכתב
    כרכב של היום ("יש לי"). התפקיד נקבע בקוד לפי המילים (car_role_cues) — הקריאה לא רצה, ושואלים."""
    if call.name not in ("match_current_car", "calculate", "select_options"):
        return None
    snapshot = ctx.snapshot
    cars = named_cars(text, snapshot.catalog)
    if not cars:
        return None
    roles = car_roles(text, cars, snapshot.extensions.car_role_cues)
    if call.name == "match_current_car":
        model = call.args.get("model")
        targets = find_catalog_car(snapshot, str(model)) if model else []
        c = ctx.state.current
        if c.source in ("catalog", "tax", "stated") and not _has_cue(text, snapshot.extensions.correction_cues):
            # the car today is known: another car without "יש לי" / a correction word is not replacing it
            # (it is probably an alternative) — ask, never confirm it with "אם זה לא הרכב שלכם"
            other = next((t for t in targets if t.catalog_code != c.catalog_code
                          and roles.get(t.catalog_code) != "current"), None)
            if other is not None:
                return other
        wrong = "alternative"
    else:
        options = available_options(snapshot, ctx.state)
        resolved = [resolve_option(snapshot, options, str(t)) for t in call.args.get("options") or []]
        targets = [o.car for o in resolved if o is not None and o.car is not None]
        wrong = "current"
    return next((c for c in targets if roles.get(c.catalog_code) == wrong), None)


def _understand_tools(state: ConversationState) -> tuple[str, ...]:
    """The tools the LLM may call: everything said is saved whatever the stage (the stage is derived from
    the data), except while calculating or waiting for the representative."""
    if next_stage(state) in (Stage.CALCULATING, Stage.WAITING_FOR_REP):
        return ()
    return LLM_TOOLS


def _is_zero(value: Any) -> bool:
    try:
        return Decimal(str(value)) == 0
    except Exception:  # noqa: BLE001 — not a number
        return False


# missing item (stage.py) → the save_profile argument a bare value answers
_ASKED_ARG = {**ARG_OF, "copay": "current_copay",
              "use_value": "current_use_value", "maintenance": "current_maintenance", "car": "car"}
_NUMERIC_ARGS = {a for a in _ASKED_ARG.values() if a != "car"}
_BARE_RE = re.compile(r"^\s*(?:בערך|כ-?|בסביבות)?\s*[\d.,]+\s*(?:%|אחוז(?:ים)?|₪|ש\"ח|שח|שקל(?:ים)?)?\s*\.?\s*$")

# save_profile arg name (asked_field) → the Q2 field name used in unknown_fields
_ASKED_Q2 = {arg: attr for arg, attr in PROFILE_ARGS.items() if attr in Q2_FIELDS}
# Hebrew words that name each Q2 field, to tell which field a "לא יודע" refers to
_Q2_WORDS = {"commissions": ("עמל",), "bonus": ("בונוס",), "pension_percent": ("פנסי",),
             "study_fund_percent": ("השתלמות",), "credit_points": ("זיכוי", "נקוד"), "children": ("ילד",)}


def _q2_fields_named(text: str) -> list[str]:
    """Which Q2 fields a message explicitly names (so 'לא יודע כמה עמלות' marks only commissions)."""
    return [f for f, words_ in _Q2_WORDS.items() if any(w in text for w in words_)]


# profile fields a question must not name once they are saved (guards.FIELD_WORDS keys = Profile attributes)
_CLOSED_FIELDS = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")
NO_CAR_ACK = "רשמתי: אין לכם רכב צמוד היום."
HAS_CAR_ACK = "רשמתי שיש לכם רכב צמוד."
MAP_UPDATED = "עדכנתי את מפת האפשרויות לפי הנתונים החדשים."
MAP_DEFERRED = "אמפה את כל האפשרויות שלכם ברגע שתגיע תשובת נציג התחבורה."
PACE_ONE = "בסדר, אשאל שאלה אחת בכל פעם."
PACE_NORMAL = "בסדר, אפשר לענות על כמה דברים יחד."


def _pace_change(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[str]:
    """"שאלה אחת כל פעם" / "הכול ביחד" (cues in extensions.yaml): the pace is stored in the state and kept
    across stages until the employee changes it. Returns the acknowledgement, or None."""
    ext = snapshot.extensions
    # whole words only: "לאטו 3" (a catalog car) is not "לאט" (QA 22/10)
    if state.pace != "normal" and says_phrase(text, ext.pace_reset_cues):
        state.pace = "normal"
        return PACE_NORMAL
    if state.pace != "one_at_a_time" and says_phrase(text, ext.pace_cues):
        state.pace = "one_at_a_time"
        return PACE_ONE
    return None


# a deferral names at most these profile fields explicitly ("את השכר אבדוק אחר כך"); otherwise it defers
# whatever is being asked now. Car details are deferred only as the group currently on screen.
_DEFERRABLE = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
               "children")
_DEFER_NAMES = {"base": "שכר הבסיס", "commissions": "העמלות", "bonus": "הבונוס", "pension_percent": "הפנסיה",
                "study_fund_percent": "קרן ההשתלמות", "credit_points": "נקודות הזיכוי", "children": "הילדים"}


def _defer_change(state: ConversationState, snapshot: RulesSnapshot, text: str, saved_this_turn: set[str]) -> str:
    """"את השכר אבדוק אחר כך": postpone a field. The field named in the message, else the fields being
    asked now. A field saved this turn is never deferred (the value wins). Returns an acknowledgement, or
    "" when nothing was deferred. The stage machine (stage.hidden_deferred) does the skipping."""
    ext = snapshot.extensions
    if not ext.defer_cues or not any(c in text for c in ext.defer_cues):
        return ""
    # which field(s)? a field named in the message (by its words), else the fields currently open
    named = [f for f in _DEFERRABLE if any(w in text for w in FIELD_WORDS.get(f, ()))]
    if not named:
        open_now = [f for f in open_profile(state.profile) if f in _DEFERRABLE]
        named = open_now
    # never defer something the employee just supplied, or something already known
    to_defer = [f for f in named if getattr(state.profile, f) is None and f not in saved_this_turn
                and f not in state.deferred_fields]
    if not to_defer:
        return ""
    state.deferred_fields.extend(to_defer)
    names = _DEFER_NAMES.get(to_defer[0], "זה") if len(to_defer) == 1 else "השדות האלה"
    return f"בסדר, נחזור ל{names} בהמשך."


def asked_field(state: ConversationState, reply: str) -> Optional[str]:
    """The one missing item the reply asks for (its question line, else its last line); None when it names
    several or none. Only missing items count, so an ack line ("רשמתי: פנסיה 6%") never binds."""
    lines = [ln for ln in reply.splitlines() if ln.strip()]
    asking = [ln for ln in lines if "?" in ln] or lines[-1:]
    if not asking:
        return None
    line = asking[-1]
    missing = [*missing_profile(state.profile), *missing_current_car(state.current)]
    named = {f for f in missing if f in _ASKED_ARG and any(w in line for w in FIELD_WORDS.get(f, ()))}
    return _ASKED_ARG[next(iter(named))] if len(named) == 1 else None


def bare_value(text: str) -> bool:
    """A message that is only a number ("34", "6%", "בערך 300 ₪"): it answers the question on screen."""
    return bool(_BARE_RE.match(text)) and any(ch.isdigit() for ch in text)


_SEND ={"לשלוח", "שלח", "שלחו", "תשלח", "כן", "send"}
_CANCEL = {"לבטל", "בטל", "ביטול", "לא", "cancel"}


def _send_answer(text: str) -> Optional[bool]:
    ws = words(text)
    if not ws or len(ws) > 3:
        return None
    return True if ws[0] in _SEND else False if ws[0] in _CANCEL else None


_ASK = {"לשאול", "תשאל", "שאל", "תשאלו", "כן", "בבקשה"}
_SKIP = {"להמשיך", "המשך", "תמשיך", "נמשיך", "לא", "בלי", "דלג"}


def _rep_offer_answer(text: str) -> Optional[bool]:
    """Typed answer to "רוצים שאשאל את נציג התחבורה?": True / False / None (something else: read normally)."""
    ws = words(text)
    if not ws or len(ws) > 4:
        return None
    return True if ws[0] in _ASK else False if ws[0] in _SKIP else None


_CHILDREN_WORDS = {"ילד אחד": 1, "אחד": 1, "שני": 2, "שתי": 2, "שניים": 2, "שלושה": 3, "שלוש": 3, "ארבעה": 4,
                   "ארבע": 4, "חמישה": 5, "חמש": 5}
# "ושני ילדים" at the end of a list too (the "ו" prefix was missed and the count was lost, live QA 22/10)
_CHILDREN_WORDS_RE = re.compile(r"(?<![א-ת])ו?(שני|שתי|שלושה|שלוש|ארבעה|ארבע|חמישה|חמש)\s+(?:ילדים|ילדות)")
# "יש לי 2 ילדים": a count right before the word. The gap fill takes it even when the parser distrusts the rest of
# the message ("6% פנסיה ו-2.5% השתלמות… ויש לי 2 ילדים" lost the children when the LLM left them out, live QA 22/10)
_CHILDREN_DIGITS_RE = re.compile(r"(?<![\d.,])(\d{1,2})\s+(?:ילדים|ילדות)")


def _ambiguity_answer(text: str) -> Optional[str]:
    """Typed answer to "אין לכם רכב, או שיש רכב וההשתתפות 0?"."""
    if no_car_span(text):
        return "no_car"
    ws = words(text)
    if ws[:2] == ["יש", "רכב"] or ws[:1] == ["0"] or ("השתתפות" in ws and "0" in ws):
        return "zero_copay"
    return None


# tool calls that carry what the employee said explicitly, or an answer to the code's own question: any stage
_EXPLICIT_ANSWERS = {"combined", "confirmed", "scale", "ambiguity"}


def _ambiguity_plan(state: ConversationState, answer: str) -> ParserPlan:
    state.pending_ambiguity = None
    if answer == "no_car":
        return ParserPlan([ToolCall("match_current_car", {"no_car": True}, "ambiguity")], True, direct=True)
    if answer == "zero_copay":
        return ParserPlan([ToolCall("save_profile", {"current_copay": "0"}, "ambiguity")], True, direct=True)
    return ParserPlan(complete=True, direct=True)


_DIGITS_RE = re.compile(r"\d+")


def rep_text_ok(text: str) -> bool:
    """A question for the representative may hold no number except a car's model year."""
    years = {m.group(0) for m in YEAR_RE.finditer(text)}
    return all(d in years for d in _DIGITS_RE.findall(text)) and 0 < len(text.strip()) <= 300


# שדות Q2 בפרופיל → שם הארגומנט ב-save_profile (כפי ש-recorded_text מצפה)
_HIDDEN_ARGS = {attr: ARG_OF[attr] for attr in Q2_FIELDS}


_ACK_FIELDS = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")


def _values(state: ConversationState) -> dict[str, Any]:
    p, c = state.profile, state.current
    out = {f: getattr(p, f) for f in _ACK_FIELDS}
    # a catalog / tax-file use value is looked up, not said: acknowledged only when the employee states it
    out.update(copay=c.copay, use_value=c.use_value if c.source == "stated" else None, maintenance=c.maintenance)
    return out


def _form_values(state: ConversationState) -> dict[str, Any]:
    """The profile values a form can change, by save_profile argument name."""
    return profile_args(state.profile)


def ack_text(changed: dict[str, Any], group: Optional[str] = None) -> str:
    """"רשמתי: קבוצה ג, שכר בסיס 18,500 ₪, פנסיה 6%, השתתפות 300 ₪." — every value saved this turn,
    from the state the tools wrote (never from what the LLM said)."""
    parts = [f"קבוצה {group}"] if group else []
    profile = {_HIDDEN_ARGS.get(f, "base_salary" if f == "base" else f): v for f, v in changed.items()
               if f in _ACK_FIELDS}
    if profile:
        parts.append(recorded_text(profile))
    for f, label in (("copay", "השתתפות"), ("use_value", "שווי שימוש"), ("maintenance", "אחזקת רכב")):
        if f in changed:
            parts.append(f"{label} {money(changed[f])}")
    return f"רשמתי: {', '.join(parts)}." if parts else ""


def _dedup_lines(reply: str) -> str:
    """Safety net on an assembled reply: never emit the same non-empty line twice. Order preserved, the
    first occurrence wins. Blank lines (paragraph breaks) are kept as-is."""
    seen: set[str] = set()
    out: list[str] = []
    for line in reply.splitlines():
        key = line.strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return "\n".join(out)


def _answers_catalog(reply: str, answers: Sequence[str], snapshot: RulesSnapshot) -> bool:
    """The reply answers each catalog question: names a car from the answer, or says it is not in the catalog."""
    def covered(answer: str) -> bool:
        if answer == NOT_IN_CATALOG:
            return "בקטלוג" in reply
        return any(c.model_name in answer for c in named_cars(reply, snapshot.catalog))
    return all(covered(a) for a in answers)


def _audit_text(state: ConversationState) -> str:
    """"כבר כתבתי לך": the truth from the state. What is saved; the flow question that follows names what is
    still missing. No apology: nothing went wrong unless the state shows it."""
    saved = ack_text({k: v for k, v in _values(state).items() if v is not None},
                     state.profile.group if state.profile.group else None)
    if state.current.label and state.current.source in ("catalog", "tax", "stated"):
        saved = (saved[:-1] + f", רכב היום {state.current.label}.") if saved else f"רשמתי: רכב היום {state.current.label}."
    elif state.current.has_car and state.current.source is None:
        car = "יש לכם רכב צמוד, וחסר לי רק הדגם שלו."
        saved = (saved[:-1] + f". {car}") if saved else f"רשמתי: {car}"
    return ("בדקתי: " + saved.replace("רשמתי: ", "עד עכשיו רשמתי ")) if saved else "בדקתי: עוד לא רשמתי אף ערך."


@dataclass(frozen=True)
class _Before:
    """המצב לפני התור — כדי לאשר במשפט מה הכלים שמרו בתור הזה (לא מה שהמודל אמר)."""

    occupation: tuple
    selected: tuple
    rank_requested: bool
    rep_reason: Optional[RepReason]
    values: dict          # profile and current-car values before the turn
    group: Optional[str] = None
    unknown: tuple = ()   # fields the employee said they do not know, before the turn
    car_unknown: tuple = ()   # today's car values the employee said they do not know, before the turn

    @classmethod
    def of(cls, state: ConversationState) -> "_Before":
        p = state.profile
        return cls((p.occupation, p.group), tuple(state.selected), state.rank_requested, blocking_rep_reason(state),
                   _values(state), current_group(state)[0], tuple(p.unknown_fields),
                   tuple(current_unknown(state.current)))

    def changed(self, state: ConversationState) -> dict[str, Any]:
        return {f: v for f, v in _values(state).items() if v is not None and v != self.values.get(f)}

    def confirmations(self, state: ConversationState, snapshot: RulesSnapshot, just_calculated: bool,
                      recorded_said: bool = False) -> list[str]:
        p, lines = state.profile, []
        # every value saved this turn is said once ("רשמתי: …"), never saved silently
        new_group = p.group if (p.group and not p.occupation and (p.occupation, p.group) != self.occupation) else None
        line = ack_text(self.changed(state), new_group)
        if line and not recorded_said:
            lines.append(line)
        unknown = [f for f in p.unknown_fields if f not in self.unknown]
        if unknown:
            lines.append(unknown_ack_text(unknown))
        conflict = state.group_conflict
        if conflict is not None and not conflict.announced:
            lines.append(group_conflict_text(conflict.stated, conflict.table, conflict.occupation))
            conflict.announced = True
        elif p.occupation and p.group and (p.occupation, p.group) != self.occupation and conflict is None:
            lines.append(confirm_group_text(p.occupation, p.group))
        if [f for f in current_unknown(state.current) if f not in self.car_unknown]:
            lines.append(CURRENT_UNKNOWN_ACK)
        if state.selected and tuple(state.selected) != self.selected and not just_calculated:
            labels = {o.id: o.label for o in available_options(snapshot, state)}
            lines.append(chosen_options_text([labels.get(i, i) for i in state.selected]))
        if state.rank_requested and not self.rank_requested:
            lines.append(RANK_DEFERRED)
        if (state.pending_rep is None and self.rep_reason is None and blocking_rep_reason(state) is not None
                and state.group_conflict is None):
            lines.append(REP_DEFERRED)
        return lines


# "בפיקנטו אני אשלם 300": a copay the employee expects for a new car (C1) — an amount with a paying word, next to an
# alternative they may compare (never today's car)
_EXPECTED_COPAY_RE = re.compile(r"(?:אשלם|נשלם|תשלם|ישלם|לשלם|משלם|משלמת|השתתפות|תעלה\s+לי|יעלה\s+לי)[^\d\n]{0,20}?"
                                r"(\d[\d,]*)")
CURRENT_UNKNOWN_ACK = ("רשמתי שזה לא ידוע. בלי הסכום הזה אין חישוב, וגם לא חישוב עם ערך משוער; הוא מופיע בתלוש השכר"
                       " החודשי. נמשיך, ונחזור אליו בסוף.")


def _stated_new_copay(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[tuple[Any, Decimal]]:
    """The option and the amount when the message gives a copay for one alternative (C1): the car situation is known
    and cars are being compared, exactly one available alternative is named, and it is not today's car."""
    if state.current.source is None or not state.selected:
        return None
    m = _EXPECTED_COPAY_RE.search(text)
    if m is None:
        return None
    options = {o.car.catalog_code: o for o in available_options(snapshot, state) if o.car is not None}
    cars = [c for c in named_cars(text, snapshot.catalog)
            if c.catalog_code in options and c.catalog_code != state.current.catalog_code]
    if len(cars) != 1:
        return None
    return options[cars[0].catalog_code], Decimal(m.group(1).replace(",", ""))


def _copay_rep_request(state: ConversationState, snapshot: RulesSnapshot, text: str) -> list[Any]:
    """The options whose copay the employee wants the representative to settle, with no amount said ("אני רוצה
    שהנציג יאשר את ההשתתפות", "אצלי סוכם אחרת, לא יודעת כמה"): results with a catalog copay on screen, the copay
    named, and a reason from `faq.copay_rep_terms`. One named car, else every such car on screen. Code decides, never
    the model (D1); an amount said is `_stated_new_copay`."""
    faq = snapshot.faq
    if not state.results or not faq.copay_rep_terms or _EXPECTED_COPAY_RE.search(text):
        return []
    ws = words(text)
    if any(w in ("למה", "מדוע", "איך", "מאיפה", "מאין") for w in ws):
        return []                  # a question about the amount is answered from the result (C5), not sent
    if not any(w.startswith(("השתתפות", "ההשתתפות", "בהשתתפות", "משלם")) for w in ws):
        return []
    if not any(w.lstrip("וש") in faq.copay_rep_terms or w in faq.copay_rep_terms for w in ws):
        return []
    options = {o.id: o for o in available_options(snapshot, state) if o.car is not None}
    shown = [r.option_id for r in state.results if r.option_id in options and r.option_id not in state.confirmed_copays
             and r.result.get("copay_source") in ("catalog", "catalog_final")]
    named = [c.catalog_code for c in named_cars(text, snapshot.catalog)]
    chosen = [o for o in shown if options[o].car.catalog_code in named] or shown
    return [options[o] for o in chosen]


class Agent:
    def __init__(self, llm: LLMChain, mode: str = "llm_first"):
        self.llm = llm
        self.mode = mode          # llm_first | parser_first

    def _llm_led(self) -> bool:
        return self.llm.available and self.mode == "llm_first"

    def greeting(self, state: ConversationState, snapshot: RulesSnapshot) -> str:
        _mark_asked(state)
        state.last_asked = list(current_group(state)[1])
        state.last_ask = GREETING.splitlines()[-1]
        return GREETING           # one question, no repeated intro later

    def run_choice(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, get_vehicle: VehicleGetter,
                   state: ConversationState, ids: list[str], label: str, record_run: RecordRun,
                   field: Optional[str] = None) -> TurnOutcome:
        """בחירה מובנית (כפתור) שכבר נבדקה מול expects: הקוד מיישם אותה ישירות — בלי הקורא ובלי מודל
        השפה. הערכים עוברים את אותם כלים ואותן בדיקות (טווחים, זכאות) כמו כל מקור אחר."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state, get_vehicle=get_vehicle)
        before = next_stage(state)
        field_ = field or build_expects(state, snapshot)["field"]
        if field_ == "ask_rep":
            if ids[0] == "skip":                     # "להמשיך בלי": back to the flow question
                state.unanswered_question, state.rep_skipped = None, True
                return self._finish(ctx, (), label, ParserPlan(complete=True, direct=True), before, record_run,
                                    choice=True)
            return self._prepare_rep_question(ctx)
        if field_ == "rep_question":
            return self._rep_question_decision(ctx, ids[0] == "send", record_run)
        if field_ == "ambiguity":
            ctx.allow_zero_copay = ids[0] == "zero_copay"     # the employee's own answer, not a guess
            plan = _ambiguity_plan(state, ids[0])
            return self._finish(ctx, (), label, plan, before, record_run, choice=True)
        state.unanswered_question = None
        if field_ == "clarification":
            waiting = state.pending_rep is not None
            plan, reply = self._clarified(state, snapshot, ids[0])
            if plan is None:
                return TurnOutcome(reply=reply, source="template", path="choice")
            if waiting:
                before = self._start_correction(ctx)
        elif field_ == "confirmation":
            answer = ids[0] if ids[0] in ("small", "thousands") else "כן" if ids[0] == "yes" else "לא"
            confirmed = _confirmation_plan(state, answer, snapshot)
            plan = confirmed[0] if confirmed else ParserPlan(complete=True)
        elif field_ == "occupation_confirmation":
            plan = _occupation_plan(ctx, ids[0])
        elif ids[0] == "none" and field_ in ZERO_ALLOWED:
            state.pending_confirmation = None
            plan = none_plan([field_])                 # "אין" על שדה בפרופיל — עם שורת "רשמתי"
        else:
            state.pending_confirmation = None
            plan = ParserPlan(choice_calls(field_, ids, snapshot), True, direct=True)
        return self._finish(ctx, (), label, plan, before, record_run, choice=True)

    def run_fields(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, get_vehicle: VehicleGetter,
                   state: ConversationState, values: dict[str, Any], label: str, record_run: RecordRun,
                   source: Optional[str] = None) -> TurnOutcome:
        """הטופס המובנה (שדות הפרופיל, כבר נבדקו): נשמרים ישירות בכלי save_profile — בלי הקורא ובלי מודל
        השפה. אחרי תיקון, השלב נגזר מהנתונים: תוצאה קיימת מחושבת מחדש."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state, get_vehicle=get_vehicle,
                          exact_amounts=True)
        before = next_stage(state)
        previous = _form_values(state)
        # A value held from chat ("פנסיה 0": לאשר?) is older than the form: a form value for the same field
        # replaces it; a held value for another field stays held (never dropped silently).
        held_before = state.pending_confirmation
        state.pending_confirmation = None
        # הטופס הוא פעולה מפורשת של העובד (גם תיקון אחרי תוצאה), לכן לא עובר דרך _run ומגבלת הכלים של
        # השלב; run_tool נשאר השער היחיד לשמירה. השלב נגזר מהנתונים: calculated_for מתאפס ← חישוב מחדש.
        facts = run_tool(ctx, "save_profile", values)
        if state.pending_confirmation is None and held_before is not None and held_before.field not in values:
            state.pending_confirmation = held_before
        saved = {k: values[k] for k in facts.get("saved", []) if k in values}   # only what really changed state
        notice = form_saved_text(previous, saved, source)
        plan = ParserPlan([], True, notice=notice, direct=True, acked=True) if notice             else ParserPlan([], True, direct=True)
        return self._finish(ctx, (), label, plan, before, record_run, choice=True)

    def run_turn(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        before = next_stage(state)

        und = understand(state, snapshot, text)
        ctx.question_message = und.question_only or text.strip().endswith("?")
        pace = _pace_change(state, snapshot, text)
        if pace is not None and len(words(text)) <= 6:
            # only the request ("שאלה אחת כל פעם"): acknowledged, kept in the state, then the next question
            return self._finish(ctx, history, text, ParserPlan(complete=True, notice=pace, direct=True), before,
                                record_run, und=Understanding(remainder=""))
        if before == Stage.WAITING_FOR_REP:
            correction, reply = self._waiting(state, snapshot, text)
            fix = _profile_value_correction_plan(state, snapshot, text) if correction is None else None
            values = {k: v for c in (fix.calls if fix else []) if c.name == "save_profile" for k, v in c.args.items()}
            if values:
                return self._save_while_waiting(ctx, text, values, before, record_run)
            if correction is None:
                # ממתינים לנציג: תשובה קצרה (או שאלת הבהרה), בלי שינוי בשלב, ברכב או בשאלות.
                # A question asked while waiting is still answered first.
                state.unanswered_question = und.unanswered[0] if und.unanswered else None
                extra = ([UNANSWERED] if und.unanswered else [snapshot.faq.redirect] if und.redirect
                         else [snapshot.faq.clarify] if und.clarify else [])
                if und.map_all:
                    state.map_requested = True       # 360°: built as soon as the representative answers
                    extra.append(MAP_DEFERRED)
                reply = "\n\n".join([*self._answers(snapshot, und)[0], *extra, reply])
                return TurnOutcome(reply=reply, source="template", path="waiting")
            reset_only = all(c.name == "match_current_car" and c.args.get("reset") for c in correction.calls)
            before = self._start_correction(ctx, keep_copay_unknown=reset_only)
            plan = correction
        elif state.pending_rep_question is not None and (send := _send_answer(text)) is not None:
            return self._rep_question_decision(ctx, send, record_run)
        elif state.unanswered_question is not None and (ask := _rep_offer_answer(text)) is not None:
            if ask:
                return self._prepare_rep_question(ctx)
            state.unanswered_question, state.rep_skipped = None, True
            plan = ParserPlan(complete=True, direct=True)        # "להמשיך": the flow question
        elif (state.offered_rank and state.pending_confirmation is None and state.stage == Stage.PRESENTED
              and words(text)[:1] in (["כן"], ["רוצים"], ["בטח"])):
            # yes to the offer; a value held for confirmation is the newer question, so its "כן" confirms it
            plan = ParserPlan([ToolCall("rank_options", {}, "offered")], True, direct=True)
        elif state.pending_ambiguity is not None and (choice := _ambiguity_answer(text)) is not None:
            ctx.allow_zero_copay = choice == "zero_copay"      # the employee's own answer, not a guess
            plan = _ambiguity_plan(state, choice)
        elif asked := _copay_rep_request(state, snapshot, text):
            plan = self._ask_rep_copay(ctx, asked)
            und = replace(und, about_results=True, remainder="", unanswered=[], clarify=False, redirect=False)
        elif und.question_only and und.anything and und.ambiguity is None:
            # only questions / "מה?" / off topic: nothing to save, and a pending question stays open.
            # With an LLM it answers (from the FAQ / STATE); without one, the FAQ template.
            plan = ParserPlan(complete=not self._llm_led(), direct=True)
        elif state.pending_confirmation is not None and (confirmed := _confirmation_plan(state, text, snapshot)) is not None:
            plan, rest = confirmed               # "כן" / "לא" על "התכוונתם ל-18,000 ₪?"
            more = fallback_plan(state, snapshot, rest) if rest else None
            if more is not None and more.complete and more.calls:
                # "כן, ופנסיה 6%": the answer, and what else the message says (read in full only)
                plan = ParserPlan([*plan.calls, *more.calls], True, notice=more.notice, direct=True,
                                  asks=plan.asks)
        elif (stated := _stated_new_copay(state, snapshot, text)) is not None:
            plan = self._note_stated_copay(ctx, *stated)
            und = replace(und, about_results=True, remainder="")
        elif state.pending_occupation is not None and (answer := _occupation_answer(state, text)) is not None:
            plan = _occupation_plan(ctx, answer)  # "כן" / מספר הצעה / "לא" על "לא מצאתי את ... בטבלה"
        elif state.pending_clarification is not None and (answer := _clarification_answer(text)) is not None:
            plan = self._clarified(state, snapshot, answer)[0]   # "הרכב שלי היום" / "חלופה לבדוק"
        else:
            # another answer: the earlier closed questions no longer apply. A value held for confirmation stays
            # held (never dropped silently) and is asked again; a new value for its field resolves it (save_profile)
            state.pending_occupation = None
            state.pending_clarification = None
            state.pending_ambiguity = None
            state.pending_rep_question = None
            if und.ambiguity is not None:
                # "אינני משתתף" before the car is known: no car, or copay 0? Asked, never guessed.
                state.pending_ambiguity = und.ambiguity
                plan = ParserPlan(complete=True, direct=True)
            else:
                # הקורא הדטרמיניסטי קודם, על ההודעה בלי השאלות שבה. מה שהוא קורא במלואו לא מגיע למודל.
                plan = fallback_plan(state, snapshot, und.remainder)
        return self._finish(ctx, history, text, plan, before, record_run, und=und, extra=[pace] if pace else ())

    def _save_while_waiting(self, ctx: ToolContext, text: str, values: dict[str, Any], before: Stage,
                            record_run: RecordRun) -> TurnOutcome:
        """A labelled correction typed while waiting for the representative ("טעיתי, השכר שלי 20,000"): saved like
        the correction form (same gate, same "עדכנתי" line); the wait goes on, and the calculation that follows the
        answer uses it. Before, it was dropped with only the waiting line."""
        previous = _form_values(ctx.state)
        facts = run_tool(ctx, "save_profile", values)
        saved = {k: values[k] for k in facts.get("saved", []) if k in values}
        notice = form_saved_text(previous, saved, None)
        plan = (ParserPlan([], True, notice=notice, direct=True, acked=True) if notice
                else ParserPlan([], True, direct=True))
        return self._finish(ctx, (), text, plan, before, record_run, choice=True)

    @staticmethod
    def _start_correction(ctx: ToolContext, keep_copay_unknown: bool = False) -> Stage:
        """העובד מתקן את הרכב בזמן המתנה: השאלות על הרכב הקודם מתבטלות, ונשאל שוב על הרכב החדש."""
        state = ctx.state
        ctx.cancel_question_ids.extend(state.pending_rep.question_ids)
        state.pending_rep = None
        # A use value that was unknown belonged to the old car. The copay stays unknown only when the same car
        # is being renamed ("התכוונתי לרכב אחר"); for another car named now, its copay is asked again.
        state.current.use_value_unknown = False
        if not keep_copay_unknown:
            state.current.copay_unknown = False
        return next_stage(state)

    def _finish(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str, plan: ParserPlan,
                before: Stage, record_run: RecordRun, choice: bool = False,
                und: Optional[Understanding] = None, extra: Sequence[str] = ()) -> TurnOutcome:
        """UNDERSTAND (parser + one LLM call) → DECIDE (code: save, then a reply plan) → SAY (the LLM
        writes the plan; the template renders it when the LLM is off, fails or breaks a check)."""
        state, snapshot = ctx.state, ctx.snapshot
        und = und or Understanding(remainder=text)
        state.link_now = None               # a link is offered on one reply only (templates._once)
        if und.all_at_once:
            state.pace = "normal"           # "all at once" is an explicit change of pace
            state.link_now = "profile_form"  # the form is the way to answer everything together
        car_before = _car_key(state)
        before_turn = _Before.of(state)
        has_car_said = und.has_car and state.current.source is None and not state.current.has_car
        if has_car_said:
            state.current.has_car = True     # "יש לי רכב חברה": a fact; the model is asked, never "אם אין רכב"
        used_llm = False
        candidate: Optional[Candidate] = None
        llm_failed = False
        gap_ack = ""
        before_turn_unanswered = state.unanswered_question
        deadline = self.llm.turn_deadline()   # אחרי LLM_TURN_DEADLINE_S — תבניות, בלי ספקים נוספים
        conflicts: list[str] = []
        parser_decides = plan.complete and (self.mode == "parser_first" or plan.calls == [] or plan.direct)
        if parser_decides or not self.llm.available:
            path = "choice" if choice else "parser" if plan.complete else "templates"
            # a direct answer to the code's own question ("לא" to a confirmation) is understood
            understood = self._execute(ctx, plan.calls) or plan.notice is not None or (plan.direct and plan.complete)
            if plan.clarify_scope:
                # a blanket "לא ידוע" / "אין לי כלום" / "הכל 5" on a grouped question: nothing is saved,
                # and from here the fields are asked one at a time, so each answer is unambiguous.
                state.pace = "one_at_a_time"
            if plan.clarify is not None:
                state.pending_clarification = plan.clarify     # "<רכב> הוא הרכב שלכם היום, או חלופה?"
                state.role_asked.append(plan.clarify.catalog_code)
                understood = True
            if self._llm_led() and not choice and next_stage(state) != Stage.CALCULATING:
                # saved in code (an answer to the bot's own question, or a message read in full); the LLM
                # still writes the reply, told what was saved
                try:
                    _, candidate = self._llm_turn(ctx, history, text, plan, deadline, {}, conflicts, und,
                                                  applied=parser_hint(plan.calls) or "nothing (the message was read)")
                    used_llm = True
                except LLMError:
                    candidate = None
        else:
            # המודל קורא קודם. תוכנית הקורא חושבה לפני כל שינוי במצב: גיבוי, ובדיקה צולבת כשנקרא במלואו.
            cross = _expected_values(snapshot, plan) if plan.complete else {}
            if not und.complaint:              # a complaint keeps the earlier offer: the reply repeats it
                state.unanswered_question = None   # otherwise the LLM offers the representative itself
            try:
                understood, candidate = self._llm_turn(ctx, history, text, plan, deadline, cross, conflicts, und)
                used_llm, path = True, "llm"
            except LLMError:
                log.warning("llm turn failed; falling back to the parser")
                understood, path, llm_failed = False, "fallback", True
            # המודל לא קרא לכלי, נכשל או בהמתנה: הקורא (מלא, או רק הגיבוי הבטוח שלו). ניחוש "כל ההודעה
            # היא הערך" רץ רק כשהמודל לא זמין — אם המודל קרא את ההודעה ולא רשם דבר, לא מנחשים.
            declined = path == "llm"
            asked = (state.pending_clarification is not None or state.pending_ambiguity is not None
                     or state.pending_confirmation is not None)
            if not understood and not conflicts and not asked and not (declined and plan.guess):
                if plan.calls or plan.notice is not None or not declined:
                    path = "fallback"            # הקורא פעל, או שהמודל נכשל
                understood = self._execute(ctx, plan.calls) or plan.notice is not None
            elif understood and not und.question_only:
                # labelled values the LLM left out ("משלמת עליה 300" next to the car) — never dropped,
                # and said, since the LLM's reply does not know about them
                values = _values(state)
                if self._fill_gaps(ctx, und.remainder, conflicts):
                    gap_ack = ack_text({k: v for k, v in _values(state).items() if v != values.get(k)})

        if before_turn.group == "Q2" and any(f in Q2_FIELDS for f in before_turn.changed(state)):
            state.q2_answered = True             # Q2 answered in part: its leftovers wait for Q3
        # "את השכר אבדוק אחר כך": postpone a field, after this turn's values were saved (so a field just
        # supplied is never deferred). The acknowledgement is added to the reply below.
        defer_ack = _defer_change(state, snapshot, text, set(before_turn.changed(state)))
        if (candidate is not None and und.unanswered and state.unanswered_question is None and not state.rep_skipped
                and not has_statement(strip_tags(candidate.text or ""))):
            # the reply only re-asks the flow question: the employee's question is not left unanswered
            state.unanswered_question = und.unanswered[0]
        # a complaint ("לא עונה לשאלה") keeps the earlier unanswered question: the offer is repeated.
        # With an LLM reply the offer is the LLM's own (propose_rep_question), never the rule-based guess.
        if candidate is None:
            state.unanswered_question = (und.unanswered[0] if und.unanswered
                                         else before_turn_unanswered if und.complaint else None)
        # "הכי משתלם" / "תמליץ" / "דרג": a request to rank ALL eligible cars. Recorded now (even mid-profile)
        # so the options step is auto-ranked and the employee is never asked to pick cars to compare. A set of
        # cars the employee already chose is left as is — ranking only fills an empty choice.
        if und.rank and not state.selected and next_stage(state) not in (Stage.WAITING_FOR_REP,):
            state.rank_requested = True
        self._open_blocking_rep(ctx)
        self._resolve_rank(ctx)
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        if und.map_all:
            state.map_requested = True       # 360°: kept; the map is built as soon as every value is known
        map_had = state.option_map is not None
        map_built = self._resolve_map(ctx)
        # the map is shown when asked for (and ready), and said to be updated when a change rebuilt it
        map_shown = ((und.map_all or (map_built and not map_had))      # asked now, or asked earlier and ready now
                     and state.option_map is not None and state.option_map.fresh)
        map_line = map_reply(state) if map_shown else MAP_UPDATED if (map_built and map_had) else ""
        state.stage = next_stage(state)
        understood = understood or state.stage != before or map_shown
        notice = _plan_notice(plan, path)
        if (notice and notice.startswith("רשמתי") and plan.fallback_notice and path in ("fallback", "templates")
                and (state.pending_confirmation is not None or ctx.range_problems)):
            # a partly read sentence: the parser's fallback notice lists every value it read, written before the
            # checks, so it also names a value held for confirmation ("עמלות 15 ₪": 15 or 15,000?) as recorded.
            # The acknowledgement comes from the state instead (only what was really saved).
            notice = None
        said = (notice or "").startswith("רשמתי") or plan.acked
        confirmations = before_turn.confirmations(state, snapshot, just_calculated, said)

        # רכב שזוהה בתור הזה (מהקורא, מהמודל או מהגיבוי) — אומרים איזה, כדי שטעות לא תעבור בשקט
        matched_car = state.current.source in ("catalog", "tax") and _car_key(state) != car_before
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        # One question per reply. A value held for confirmation ("לאשר 0 נקודות זיכוי?") is that question; a
        # value refused by its range is then said as a statement, and its field is asked again afterwards.
        held = state.pending_confirmation is not None
        refused = [range_refused_text(*p) for p in ctx.range_problems] if held else []
        problems = [conflict_text(c) for c in conflicts] + (
            [] if held else [range_problem_text(*p) for p in ctx.range_problems])
        asks_field = plan.asks or (ctx.range_problems[0][0] if ctx.range_problems and not held and not conflicts
                                   else None)
        answers, terms = self._answers(snapshot, und)
        # "אין רכב": said now, or said again ("כתבתי שאין לי רכב"): acknowledged first; said again while the
        # maintenance question is open, the question is explained in its own words (never a stale field)
        restated_no_car = state.current.source == "none" and car_before[0] == "none" and bool(no_car_span(text))
        no_car_lines: list[str] = []
        if state.current.source == "none" and (restated_no_car or car_before[0] != "none"):
            no_car_lines.append(NO_CAR_ACK)
            entry = snapshot.faq.get("maintenance")
            if restated_no_car and entry is not None and current_group(state)[1] == ["maintenance"]:
                no_car_lines.append(entry.answer)
        if has_car_said and state.current.source is None and not state.current.choices:
            no_car_lines.append(HAS_CAR_ACK)
        lines = [*extra, *([defer_ack] if defer_ack else []), *no_car_lines,
                 *(["צודקים, סליחה."] if und.complaint else []),
                 *([_audit_text(state)] if und.audit else []),
                 *([ALL_AT_ONCE] if und.all_at_once else []),
                 *([snapshot.faq.redirect] if und.redirect and not und.complaint else []),   # the rep offer: plan_reply
                 # a truly unclear question: one line, no buttons, never the representative
                 *([snapshot.faq.clarify] if und.clarify and not (und.redirect or und.unanswered or und.complaint)
                   else [])]
        about_results = und.about_results and state.stage == Stage.PRESENTED and bool(state.results)
        if about_results and plan.notice is None:
            lines.append(results_explain_text(state, und.result_focus))
        # a request the bot cannot do yet ("תשווה ביניהם" before the profile is complete): what is needed
        if (und.compare and not just_calculated and not map_shown
                and state.stage not in (Stage.PRESENTED, Stage.WAITING_FOR_REP) and RANK_DEFERRED not in confirmations):
            lines.append(compare_needs_text(state))
        def build_plan():
            built = plan_reply(
                state, snapshot, understood=understood or bool(problems) or und.anything,
                just_calculated=just_calculated, matched_car=matched_car, notice="\n".join(problems) or notice,
                rep_opened=rep_opened, notice_asks=bool(problems) or plan.asks is not None, cta_field=asks_field,
                confirmations=[*confirmations, *refused], answers=list(zip(answers, terms)),
                follow_up=und.presence, explain=und.help or len(no_car_lines) > 1, extra=lines,
                about_results=about_results)
            if map_line:
                built.ack.append(map_line)  # after what was saved and identified this turn
            if map_shown and state.stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
                built.ask = []      # the map answered the request; the call to action still offers a comparison
            return built

        reply_plan = build_plan()
        rep_skipped = state.rep_skipped
        _mark_asked(state)
        # the reply about unknown fields alternates its wording: "לא יודע" twice never gets the same text
        state.unknown_told = state.unknown_told + 1 if current_group(state)[0] == "unknown" else 0
        state.misses = 0 if (understood or problems or und.anything) else state.misses + 1
        state.rep_skipped = False
        state.offered_rank = ((reply_plan.fixed or reply_plan.cta == NEXT_AFTER_RESULTS)
                              and state.stage == Stage.PRESENTED and not state.ranked_count)
        state.last_asked = [] if just_calculated else list(current_group(state)[1])
        state.last_ask = reply_plan.ask[-1] if reply_plan.ask else ""
        link = call_to_action(state, snapshot)[1] if not reply_plan.fixed else None
        if link:
            state.link_now = link
            if link not in state.links_shown:
                state.links_shown.append(link)
        requests = dict(question_requests=ctx.question_requests, cancel_question_ids=ctx.cancel_question_ids,
                        copay_selection=ctx.copay_selection)
        draft = reply_plan.text()
        # results and the rep messages keep their exact template text; buttons get the template (fast)
        if (choice or reply_plan.fixed or rep_opened or candidate is None or not self.llm.available
                or self.mode == "parser_first" or map_shown):     # the map's summary is code's, like results
            say = ("outage" if llm_failed else "not_tried" if candidate is None or not used_llm else "fixed")
            state.asked_field = asked_field(state, draft)
            return TurnOutcome(reply=draft, source="template", just_calculated=just_calculated, path=path,
                               say=say, **requests)
        reply, source, blocked, say = self._guarded(candidate, draft, ctx, history, text, deadline, conflicts,
                                                    check_repeat=not (und.faq_ids or und.catalog or restated_no_car),
                                                    must_answer=bool(und.audit or und.all_at_once or und.faq_ids
                                                                     or und.about_results
                                                                     or und.catalog or und.unanswered or und.help
                                                                     or und.complaint or restated_no_car))
        if (source == "template" and und.unanswered and state.unanswered_question is None and not rep_skipped
                and not und.redirect):
            # the LLM's reply answered the employee's question, but a guard rejected it: the template that replaces
            # it must not drop the question. Code found a concrete question about the arrangements (scope "org"),
            # so the template offers the representative, as it does without an LLM (live QA 22/10).
            state.unanswered_question = und.unanswered[0]
            offered = build_plan()
            reply = offered.text()
            state.last_ask = offered.ask[-1] if offered.ask else ""
        if gap_ack and source == "llm":
            reply = f"{gap_ack}\n{reply}"
        if source == "llm" and und.catalog and not _answers_catalog(reply, und.catalog, snapshot):
            # the catalog sentence was trimmed by a guard: the code's answer (from the catalog) comes first
            reply = "\n".join([*und.catalog, reply])
        if source == "llm":
            reply = _dedup_lines(reply)       # safety net: the live model sometimes repeats a line
        state.asked_field = asked_field(state, reply)
        return TurnOutcome(reply=reply, source=source, guard_blocked=blocked, just_calculated=just_calculated,
                           path=path, say=say, **requests)

    @staticmethod
    def _answers(snapshot: RulesSnapshot, und: Understanding) -> tuple[list[str], list[str]]:
        entries = [e for e in (snapshot.faq.get(i) for i in und.faq_ids) if e is not None]
        return ([e.answer for e in entries] + und.catalog,
                [e.term for e in entries] + ["קטלוג הרכבים"] * len(und.catalog))

    def _fill_gaps(self, ctx: ToolContext, text: str, conflicts: Sequence[str] = ()) -> bool:
        """Labelled values the parser reads with certainty ("פנסיה 6%", "משלמת 300") that no tool saved
        this turn: saved now, so nothing the employee said is dropped."""
        if not text:
            return False
        state, ext = ctx.state, ctx.snapshot.extensions
        reading = read_profile(text, ctx.snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
        current = profile_args(state.profile)
        # labelled values ("פנסיה 6%") and explicit zeros ("אין לי ילדים") — never a bare number
        said = {**{k: v for k, v in reading.fields.items() if _is_zero(v)}, **reading.labelled}
        kids = _CHILDREN_WORDS_RE.search(text)        # "שני ילדים": a number in words
        if kids and current["children_under_23"] is None:
            said["children_under_23"] = _CHILDREN_WORDS[kids.group(1)]
        kids = _CHILDREN_DIGITS_RE.search(mask_ages(text))   # "2 ילדים" ("ילדים מתחת גיל 23" is not a count)
        if kids and current["children_under_23"] is None and "children_under_23" not in said:
            said["children_under_23"] = int(kids.group(1))
        args = {k: v for k, v in said.items()
                if k in current and current[k] is None and k not in conflicts}
        copay = parse_copay(text)
        if (copay is not None and state.current.copay is None and "current_copay" not in conflicts
                and (state.current.source or state.current.query)):
            args["current_copay"] = str(copay)
        if not args:
            return False
        args["approximate_fields"] = [k for k in reading.approximate if k in args]
        return _recorded(self._run(ctx, ToolCall("save_profile", args, "gap")))

    # ---------- חילוץ ----------

    def _llm_turn(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str, plan: ParserPlan,
                  deadline: Optional[float], cross: dict[str, Any], conflicts: list[str],
                  und: Understanding, applied: str = "") -> tuple[bool, Candidate]:
        """The LLM leads the turn: it saves through tools (each call checked in code, exactly as before),
        answers and asks; the reply it writes is guarded in _finish before it is sent."""
        state, snap = ctx.state, ctx.snapshot
        recorded = [False]

        def execute(call: ToolCall) -> dict[str, Any]:
            if call.name == "calculate":
                call = ToolCall("select_options", call.args, call.id)
            if call.name not in LLM_TOOLS:
                return {"ok": False, "error": "unknown tool"}
            if call.name == "propose_rep_question" and state.rep_skipped:
                return {"ok": False, "error": "the employee chose to continue without the representative"}
            if call.name == "propose_rep_question" and not und.unanswered:
                # only a concrete question about the cars or the arrangements that code found (question_scope
                # "org") may reach the representative: never an off-topic, unclear, follow-up or net question,
                # whatever the model thinks
                return {"ok": False, "error": "not a question for the representative: it is not a concrete question "
                                              "about company cars, eligibility or the arrangements. If it is off "
                                              "topic, say in one line what you can help with; if it is unclear, "
                                              "ask the employee to say it again."}
            if call.name == "propose_rep_question" and not und.unanswered and (
                    und.all_at_once or und.catalog or und.faq_ids):
                # code already answers it (the catalog / FAQ), or it is about the conversation itself
                return {"ok": False, "error": "not a question for the representative: it is answered in this "
                                              "reply by the system, or it is about the conversation itself "
                                              "(explain in one line why you ask in steps)"}
            checked = self._check_call(ctx, call, text, und)
            if checked is None:
                return {"ok": False, "error": "not saved: the message does not say this; ask the employee"}
            blocked = _role_conflict(ctx, checked, text)
            if blocked is not None:
                if blocked.catalog_code not in state.role_asked:
                    state.pending_clarification = CarClarification(catalog_code=blocked.catalog_code,
                                                                   label=blocked.label)
                    state.role_asked.append(blocked.catalog_code)
                return {"ok": False, "error": f"not saved: ask whether {blocked.label} is the car today or "
                                              "an alternative to check"}
            before = len(conflicts)
            checked = _cross_check(ctx, checked, cross, conflicts)
            facts = self._run(ctx, checked, relaxed=True)
            if len(conflicts) > before:
                facts = {**facts, "conflict": conflicts[before:],
                         "note": "two different readings of a value: ask the employee which one is right"}
            if checked.name != "propose_rep_question":
                recorded[0] = recorded[0] or _recorded(facts)
            return facts

        messages = [system_message(state, snap, "" if applied or not plan.complete else parser_hint(plan.calls), applied),
                    *history[-HISTORY:], user_message(text)]
        cand = run_rounds(self.llm, messages, _understand_tools(state), execute, deadline, force_on_claim=not applied)
        return recorded[0], cand
    @staticmethod
    def _grounded(ctx: ToolContext, args: dict[str, Any], text: str) -> Optional[dict[str, Any]]:
        """Only what the employee said is saved (grounding.py): numbers appear in the message (digits or Hebrew
        words), a zero has a zero cue, the group / occupation / drive are named. The rest is refused, never
        remapped; None when nothing grounded is left."""
        snap = ctx.snapshot
        ext = snap.extensions
        kept, refused = dict(args), []
        # "ילדים מתחת גיל 23": the age is part of the field's name; "45 או 45 אלף": amounts offered as alternatives
        # are not stated, so neither grounds a value (the employee is asked which one)
        numbers_text = without_alternatives(mask_ages(text))
        for key in _NUMERIC_ARGS:
            value = kept.get(key)
            if value is None:
                continue
            ok = (zero_grounded(key, text, ext.zero_cues) if _is_zero(value)
                  else grounded_number(value, numbers_text))
            if not ok:
                refused.append(key)
        if kept.get("eligibility_group") and not group_named(text, snap.calc.group_order, FAMILY_GROUP):
            refused.append("eligibility_group")
        if kept.get("occupation"):
            name = match_occupation(snap.occupations.keys(), str(kept["occupation"]))
            hit = occupation_match(snap.occupations.keys(), text)
            if name and not occupation_named(name, text, hit[0] if hit else None):
                refused.append("occupation")
        if kept.get("current_drive") and match_drive(text) is None:
            refused.append("current_drive")
        for key in refused:
            kept.pop(key)
        if refused:
            log.info("not said by the employee, refused: %s", ",".join(sorted(refused)))
        meaningful = {k for k, v in kept.items() if k != "approximate_fields" and v not in (None, [], False)}
        return kept if meaningful or not refused else None

    @staticmethod
    def _options_said(ctx: ToolContext, options: Sequence[Any], text: str) -> bool:
        """The employee named the alternatives (or their numbers in the list, or asked for the ranking)."""
        snap = ctx.snapshot
        available = available_options(snap, ctx.state)
        named = {c.catalog_code for c in named_cars(text, snap.catalog)}
        if any(w in text.lower() for w in OPT_OUT_WORDS):
            named.add(OPT_OUT_ID)
        resolved = [resolve_option(snap, available, str(t)) for t in options]
        codes = [o.id for o in resolved if o]
        if option_named(codes, named, text, ("הכי משתלם", "משתלם", "המלצ", "תמליץ", "דרג")):
            return True
        # described by its drive ("את החשמלי"): every chosen car has the drive the employee said
        # (a Hebrew word with its "ה" / "ו" / "ב" prefix: "החשמלי")
        drive = match_drive(" ".join(t[1:] if len(t) > 3 and t[0] in "הובל" else t for t in words(text)) + " " + text)
        return bool(codes and drive is not None and all(o.drive == drive for o in resolved if o and o.id != OPT_OUT_ID))

    @staticmethod
    def _check_call(ctx: ToolContext, call: ToolCall, text: str, und: Understanding) -> Optional[ToolCall]:
        """Hard rules on what the model asked to save: a field said to exist without an amount is never 0,
        and "no car" needs the words for it (otherwise ask: no car, or copay 0?)."""
        state = ctx.state
        if call.name == "save_profile":
            presence = {"commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
                        "study_fund_percent": "study_fund_percent", "children": "children_under_23"}
            drop = {presence[f] for f in und.presence if f in presence}
            args = {k: v for k, v in call.args.items()
                    if not (k in drop and v is not None and _is_zero(v))}
            # a copay while code asks "no car, or copay 0?" — or a copay of 0 before any car is known — is
            # never saved: "אינני משתתף" may mean no car at all (the question decides, never the model)
            # (und.ambiguity: "אינני משתתף" with no car named in the message and none known)
            if args.get("current_copay") is not None and (
                    state.pending_ambiguity is not None or und.ambiguity is not None):
                args.pop("current_copay")
                state.pending_ambiguity = NO_CAR_OR_ZERO
                log.info("copay 0 before the car is known; asked, not saved")
                if not any(v not in (None, [], False) for k, v in args.items() if k != "approximate_fields"):
                    return None
            # A blanket answer to a grouped Q2 is never spread silently across every field. The model tends
            # to over-apply a bare "לא ידוע" (unknown_fields=all) or a sweeping "אין לי כלום" / "הכל 5"
            # (zeros / one value on several fields). Policy:
            #   - a named field ("לא יודע כמה עמלות")                → that field only
            #   - one Q2 field on screen                             → that field
            #   - several on screen + an explicit "all" scope        → all of them (the employee said so)
            #   - several on screen, no scope ("לא ידוע", "הכל 5")   → ambiguous: save nothing, ask one at a time
            on_screen = [f for f in paced(state, current_group(state)[1]) if f in Q2_FIELDS]
            grouped = len(on_screen) > 1
            if args.get("unknown_fields"):
                named = _q2_fields_named(text)
                asked_q2 = _ASKED_Q2.get(state.asked_field or "")
                if named:
                    args = {**args, "unknown_fields": [f for f in args["unknown_fields"] if f in named]}
                elif asked_q2:
                    args = {**args, "unknown_fields": [asked_q2]}
                elif not grouped and on_screen:
                    args = {**args, "unknown_fields": on_screen}
                elif grouped and not has_all_scope(text):
                    log.info("blanket 'unknown' on a grouped question: not spread; asking one at a time")
                    state.pace = "one_at_a_time"
                    args = {k: v for k, v in args.items() if k != "unknown_fields"}
            # the model spread one value / zeros across several Q2 fields for a sweeping message with no
            # per-field wording ("הכל 5", "אין לי כלום"): refuse the spread, ask one at a time
            if grouped and not _q2_fields_named(text):
                spread = [k for k in args if k in _ASKED_Q2 and args.get(k) is not None]
                if len(spread) > 1 and (has_all_scope(text) or bare_value(text)):
                    log.info("blanket value spread across %d Q2 fields: refused; asking one at a time",
                             len(spread))
                    state.pace = "one_at_a_time"
                    args = {k: v for k, v in args.items() if k not in spread}
            if args.get("current_copay") is not None and _stated_new_copay(state, ctx.snapshot, text) is not None:
                args = {k: v for k, v in args.items() if k != "current_copay"}
                if not any(v not in (None, [], False) for k, v in args.items() if k != "approximate_fields"):
                    return None
            args = Agent._grounded(ctx, args, text)
            if args is None:
                return None
            # a bare value ("34") answers only the field on screen: any other field is refused, never remapped
            bound = state.asked_field
            if bound and bare_value(text):
                wrong = [k for k, v in args.items() if k in _NUMERIC_ARGS and v is not None and k != bound]
                if wrong:
                    log.info("bare value for %s refused (asked %s)", ",".join(wrong), bound)
                    return None
            elif (bare_value(text) and current_group(state)[0] == "Q1" and not asks_base(state)
                  and args.get("base_salary") is not None):
                # the greeting asks the group, not the pay: "16,400" alone is not guessed to be the base (22/10)
                log.info("bare value for base_salary refused (the group or the role is asked)")
                return None
            return ToolCall(call.name, args, call.id)
        if call.name == "rank_options" and not und.rank:
            # ranking replaces the cars to compare: only when the employee asked for it ("הכי משתלם", "דרג"), never
            # on "השווה את כל האפשרויות" (the map keeps the choice) — live transcript 22/10
            log.info("rank_options without a ranking request; refused")
            return None
        if call.name == "select_options" and not Agent._options_said(ctx, call.args.get("options", []), text):
            log.info("alternatives not named by the employee; not selected")
            return None
        if call.name == "match_current_car" and call.args.get("model"):
            # the model shortened what the employee wrote ("קורולה" for "קורולה קרוס"): the car the message
            # itself names, when that name is unambiguous; a bare "קורולה" from the employee is still asked
            candidates = {c.catalog_code for c in related_cars(ctx.snapshot, str(call.args["model"]))}
            said = [c for c in named_cars(text, ctx.snapshot.catalog) if c.catalog_code in candidates
                    and len(related_cars(ctx.snapshot, matched_name(text, c) or c.model_name)) == 1]
            if len(candidates) > 1 and len(said) == 1:
                return ToolCall(call.name, {**call.args, "model": said[0].catalog_code, "registration_year": None},
                                call.id)
        if call.name == "match_current_car" and call.args.get("no_car") and not no_car_span(text):
            if state.current.source is None:
                state.pending_ambiguity = NO_CAR_OR_ZERO
            log.info("no_car without the words for it; not saved")
            return None
        return call

    def _run(self, ctx: ToolContext, call: ToolCall, relaxed: bool = False) -> dict[str, Any]:
        """רק הכלים שהשלב הנוכחי מתיר. כלי אחר מתועד ונדחה. relaxed (UNDERSTAND): every data tool except
        while calculating or waiting — a value said early is saved, never dropped; the stage still
        comes from the data."""
        allowed = _understand_tools(ctx.state) if relaxed else allowed_tools(ctx.state)
        if call.name not in allowed:
            log.info("tool %s rejected at stage %s", call.name, next_stage(ctx.state).value)
            return {"ok": False, "error": "tool not allowed at this stage"}
        return run_tool(ctx, call.name, call.args)
    def _execute(self, ctx: ToolContext, calls: Sequence[ToolCall]) -> bool:
        # a call tagged "combined" reads an explicitly-stated fact (e.g. "no company car" said during the
        # salary questions), and an answer to the code's own question ("כן" to "התכוונתם ל-45,000 ₪?") is valid
        # at any stage: both run through the relaxed stage gate (a confirmed salary correction after a result was
        # refused as "tool not allowed" — QA 22/10)
        return any([_recorded(self._run(ctx, call, relaxed=(call.id in _EXPLICIT_ANSWERS))) for call in calls])

    # ---------- נציג ----------

    def _waiting(self, state: ConversationState, snapshot: RulesSnapshot,
                 text: str) -> tuple[Optional[ParserPlan], str]:
        """הודעה בזמן המתנה לנציג. (תיקון לביצוע, תשובה). תיקון רק כשבטוחים — אחרת שואלים."""
        waiting_reply = render(state, snapshot, understood=True, just_calculated=False)
        pending = state.pending_rep
        if pending is None or pending.reason not in (RepReason.CURRENT_COPAY, RepReason.CURRENT_USE_VALUE):
            return None, waiting_reply

        if state.pending_clarification is not None:
            answer = _clarification_answer(text)
            if answer is not None:
                return self._clarified(state, snapshot, answer)

        if _has_cue(text, snapshot.extensions.correction_cues):
            plan = correction_plan(state, snapshot, text)
            if plan is not None and plan.complete and plan.calls:
                return replace(plan, direct=True), ""
            return None, waiting_reply

        others = [c for c in named_cars(text, snapshot.catalog) if c.catalog_code != state.current.catalog_code]
        if not others:
            return None, waiting_reply
        calls = self._llm_waiting_correction(state, snapshot, text)
        if calls:
            return ParserPlan(list(calls), True, direct=True), ""
        # בלי מודל, או שהמודל לא בטוח: לא מנחשים — שואלים, וזוכרים על איזה רכב
        state.pending_clarification = CarClarification(catalog_code=others[0].catalog_code, label=others[0].label)
        return None, clarify_car_text(others[0].label)

    @staticmethod
    def _clarified(state: ConversationState, snapshot: RulesSnapshot,
                   answer: str) -> tuple[Optional[ParserPlan], str]:
        """תשובה ל"הוא הרכב שלכם היום, או חלופה?": mine → התאמה (מחדש); alternative → בהמתנה לנציג
        בתור לחישוב, וב-Q3 נבחרת מיד (ומחושבת כשהרכב של היום ידוע)."""
        clarification = state.pending_clarification
        state.pending_clarification = None
        if answer == "mine":
            car = next(c for c in snapshot.catalog if c.catalog_code == clarification.catalog_code)
            call = ToolCall("match_current_car", {"model": car.model_name,
                                                  "registration_year": clarification.reg_year}, "clarified")
            return ParserPlan([call], True, direct=True), ""
        if state.pending_rep is None:
            call = ToolCall("calculate", {"options": [*state.selected, clarification.catalog_code]}, "clarified")
            return ParserPlan([call], True, direct=True), ""
        if clarification.catalog_code not in state.queued_options:
            state.queued_options.append(clarification.catalog_code)
        return None, queued_option_text(clarification.label)

    def _llm_waiting_correction(self, state: ConversationState, snapshot: RulesSnapshot,
                                text: str) -> Optional[tuple[ToolCall, ...]]:
        """המודל מקבל רק את match_current_car: יקרא לו רק אם ההודעה מתקנת את הרכב של היום."""
        if not self.llm.available:
            return None
        # the model may call match_current_car only when the message is a correction of the car today. Code
        # double-checks: a bare alternative name ("אטו 3") with no correction cue and no "my car" wording is
        # NOT a correction — it is probably an alternative, so we ignore the call and ask (never guess).
        ws = words(text)
        says_my_car = ("הרכב" in ws and "שלי" in ws) or ("יש" in ws and "לי" in ws)
        if not (_has_cue(text, snapshot.extensions.correction_cues) or says_my_car):
            return None
        system = WAITING_CORRECTION_PROMPT.format(car=state.current.label or "")
        try:
            response = self.llm.complete([ChatMessage("system", system), ChatMessage("user", text)],
                                         [TOOL_SPECS["match_current_car"]], deadline=self.llm.turn_deadline())
        except LLMError:
            return None
        calls = tuple(c for c in response.tool_calls if c.name == "match_current_car")
        return calls or None

    def _request_new_car_copay(self, ctx: ToolContext, option: Any, policy: str, replace: bool = False) -> None:
        """The representative is asked the copay of a new car compared (C1): the catalog amount to confirm, or — when
        the catalog has none (C2) — the amount itself; a copay the employee expects goes with it. Context: the group,
        the car, the catalog amount and that expectation only — never pay data."""
        state = ctx.state
        default = option.car.default_copay
        stated = state.stated_new_copays.get(option.id)
        context = {"group": state.profile.group, "car_label": option.label, "catalog_code": option.car.catalog_code,
                   "suggested_amount": str(default) if default is not None else None,
                   "employee_stated_amount": str(stated) if stated is not None else None}
        if default is None:
            text = f"מה ההשתתפות החודשית של העובד ב-{option.label}? אין לרכב הזה סכום בקטלוג."
            mode = Mode.REQUIRED
        else:
            text = f"מה ההשתתפות החודשית של העובד ב-{option.label}? לפי הקטלוג: {default} ₪"
            mode = Mode.REQUIRED if policy == "REP_REQUIRED" else Mode.CONFIRM
        if stated is not None:
            text += f". העובד ציין שהוא מצפה להשתתפות של {stated} ₪"
        self._request(ctx, MissingField.NEW_CAR_COPAY, mode, AnswerType.MONEY, text, context,
                      str(default) if default is not None else None, option.id, replace=replace)

    def _ask_rep_copay(self, ctx: ToolContext, options: list[Any]) -> ParserPlan:
        """A question per car (open_question keeps one); the cards show it waiting until the answer."""
        state = ctx.state
        for option in options:
            self._request_new_car_copay(ctx, option, ctx.snapshot.extensions.copay_policy)
            if option.id not in state.rep_copay_asked:
                state.rep_copay_asked.append(option.id)
            for item in state.results:
                if item.option_id == option.id:
                    item.result.update(copay_source="catalog", copay_pending=True)
        text = copay_rep_request_text([o.label for o in options], [o.car.default_copay for o in options])
        return ParserPlan(complete=True, notice=text, direct=True)

    def _note_stated_copay(self, ctx: ToolContext, option: Any, amount: Decimal) -> ParserPlan:
        """A copay the employee expects for a new car (C1): kept apart from the calculation, which stays on the catalog
        amount until the representative answers; the open question is replaced with one that carries it."""
        state = ctx.state
        state.stated_new_copays[option.id] = amount
        if option.id in state.calculated_for and option.id not in state.confirmed_copays:
            self._request_new_car_copay(ctx, option, ctx.snapshot.extensions.copay_policy, replace=True)
            if option.id not in state.rep_copay_asked:
                state.rep_copay_asked.append(option.id)
            for item in state.results:          # shown next to the catalog amount on the card, never used
                if item.option_id == option.id:
                    item.result.update(employee_stated_copay=str(amount), copay_source="catalog", copay_pending=True)
        return ParserPlan(complete=True, notice=stated_copay_text(option.label, amount, option.car.default_copay),
                          direct=True)

    @staticmethod
    def _request(ctx: ToolContext, field: MissingField, mode: Mode, answer_type: AnswerType, text: str,
                 context: dict[str, Optional[str]], suggested: Optional[str] = None, option_id: str = "",
                 replace: bool = False) -> None:
        """שאלה לנציג. ההקשר: רק מה שהנציג צריך (קבוצה, רכב, סכום מוצע, עיסוק) — בלי שכר."""
        clean = {k: v for k, v in context.items() if v is not None}
        ctx.question_requests.append(QuestionRequest(field, mode, answer_type, text, clean, suggested, option_id,
                                                     replace))

    @staticmethod
    def _resolve_map(ctx: ToolContext) -> bool:
        """360°: (re)build the option map once it was asked for, when it is missing or out of date and every
        value the engine needs is known (never on a guessed value). True when it was built now."""
        state = ctx.state
        if not state.map_requested or (state.option_map is not None and state.option_map.fresh):
            return False
        if not map_ready(state):
            return False
        try:
            state.option_map = build_option_map(ctx.snapshot, state)
        except ToolError:
            return False
        return True

    @staticmethod
    def _resolve_rank(ctx: ToolContext) -> None:
        """"הכי משתלם" שנרשם לפני שהפרטים היו ידועים: עכשיו, כשרק החלופות חסרות, הבחירה נעשית."""
        state = ctx.state
        if state.rank_requested and not state.selected and next_stage(state) == Stage.CHOOSE_OPTIONS:
            if not run_tool(ctx, "rank_options", {}).get("ok"):
                state.rank_requested = False     # אין חלופות זכאיות לדירוג — שואלים על החלופות

    def _open_blocking_rep(self, ctx: ToolContext) -> None:
        """שאלה לנציג רק אחרי שהפרופיל מלא: קודם משלימים את מה שהעובד יודע. Only a group that contradicts the
        occupations table blocks the conversation (C4); today's car, its copay and its use value are the employee's
        own data and are asked from the employee, never the representative (rule A, 22/10)."""
        state = ctx.state
        if state.pending_rep is not None or open_profile(state.profile) or state.current.source is None:
            return              # first the profile and the car itself; a question about "which car?" waits
        if blocking_rep_reason(state) != RepReason.ELIGIBILITY_GROUP:
            return
        conflict = state.group_conflict
        groups = ", ".join(ctx.snapshot.calc.group_order)
        self._request(ctx, MissingField.ELIGIBILITY_GROUP, Mode.REQUIRED, AnswerType.TEXT,
                      f"העובד ציין קבוצת זכאות {conflict.stated}, ולפי טבלת העיסוקים התפקיד \"{conflict.occupation}\" "
                      f"שייך לקבוצה {conflict.table}. איזו קבוצה חלה עליו? ענו באות הקבוצה ({groups}).",
                      {"occupation_text": conflict.occupation, "stated_group": conflict.stated,
                       "table_group": conflict.table})
        state.pending_rep = PendingRep(reason=RepReason.ELIGIBILITY_GROUP, resume_stage=Stage.COLLECT_PROFILE,
                                       labels=[])

    # ---------- תשובות הנציג ----------

    def apply_answer(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                     event: QuestionAnswered, record_run: RecordRun) -> TurnOutcome:
        """מיישם תשובת נציג על מצב השיחה. בלי מודל שפה; כל מספר בהודעה — מהתשובה או מהמנוע."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        field, qid = event.missing_field, event.question_id
        lines: list[str] = []
        show_results = False
        pending = state.pending_rep
        was_waiting_on_copay = pending is not None and pending.reason == RepReason.NEW_CAR_COPAY
        if pending is not None and qid in pending.question_ids:
            pending.question_ids.remove(qid)
            if not pending.question_ids:
                state.pending_rep = None
        amount = (Decimal(event.value) if field not in (MissingField.OCCUPATION_OPT_OUT, MissingField.GENERAL_QUESTION,
                                                        MissingField.ELIGIBILITY_GROUP)
                  else None)

        if field == MissingField.NEW_CAR_COPAY:
            default = next((c.default_copay for c in snapshot.catalog if c.catalog_code == event.option_id), None)
            state.confirmed_copays[event.option_id] = ConfirmedCopay(
                value=amount, effective_date=event.effective_date, changed=default is None or amount != default)
            if state.option_map is not None:
                state.option_map.fresh = False      # this option's copay is no longer the catalog's
            line = self._apply_new_car_copay(ctx, event, amount, record_run)
            if line:
                lines.append(line)
            show_results = was_waiting_on_copay and state.pending_rep is None   # REP_REQUIRED: עכשיו מציגים
        elif field in (MissingField.CURRENT_COPAY, MissingField.CURRENT_USE_VALUE):
            c = state.current
            if field == MissingField.CURRENT_COPAY:
                c.copay, c.copay_unknown = amount, False
            else:
                c.source, c.label = "stated", c.label or safe_car_label(c.query)
                c.use_value, c.use_value_unknown = amount, False
            invalidate_results(state)
            lines.append(rep_current_text(field.value, c.label or "", amount))
        elif field == MissingField.OCCUPATION_OPT_OUT:
            allowed = event.value == "true"
            state.profile.rep_opt_out_allowed = allowed
            invalidate_results(state)
            lines.append(rep_opt_out_text(allowed))
        elif field == MissingField.MAINTENANCE_AMOUNT:
            state.profile.rep_opt_out_maintenance = amount
            invalidate_results(state)
            lines.append(rep_maintenance_text(amount))
        elif field == MissingField.ELIGIBILITY_GROUP:
            group = match_group(snapshot.calc.group_order, str(event.value)) or str(event.value).strip()
            state.profile.group = group
            state.group_conflict = None
            invalidate_results(state)
            lines.append(rep_group_text(group))
        elif field == MissingField.GENERAL_QUESTION:
            # the representative's own words; kept with the question, so asking it again shows this answer
            asked = state.general_questions.get(event.option_id)
            if asked is not None:
                asked.answer = event.value
            lines.append(f"נציג התחבורה ענה על השאלה שלכם:\n{event.value}")
        note = rep_note_text(event.note)
        if note:
            lines.append(note)               # the representative's own words to the employee, plain text

        state.applied_answers.append(qid)
        if state.pending_rep is None and state.queued_options:
            # חלופות שהעובד ביקש בזמן ההמתנה — מחושבות עכשיו
            available = {o.id for o in available_options(snapshot, state)}
            queued = [o for o in state.queued_options if o in available][:MAX_OPTIONS]
            state.queued_options = []
            if queued:
                state.selected, state.calculated_for = queued, []
        self._resolve_rank(ctx)
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        map_had = state.option_map is not None
        if self._resolve_map(ctx):
            # the representative's answer completed the map (presented now, as it was asked for) or changed it
            lines.append(map_reply(state) if not map_had else MAP_UPDATED)
        state.stage = next_stage(state)
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        if field == MissingField.NEW_CAR_COPAY:
            if show_results and state.results:
                lines.append(results_text(state))
        elif field == MissingField.GENERAL_QUESTION and state.stage == Stage.PRESENTED:
            pass                                   # the results stay on screen; nothing else to ask
        elif not (field == MissingField.OCCUPATION_OPT_OUT and state.profile.rep_opt_out_allowed):
            lines.append(render(state, snapshot, understood=True, just_calculated=just_calculated,
                                rep_opened=rep_opened))
        return TurnOutcome(reply="\n\n".join(x for x in lines if x), source="rep", just_calculated=just_calculated,
                           path="rep", question_requests=ctx.question_requests, copay_selection=ctx.copay_selection)

    def _apply_new_car_copay(self, ctx: ToolContext, event: QuestionAnswered, amount: Decimal,
                             record_run: RecordRun) -> Optional[str]:
        """אותו סכום — האישור נרשם בהנחות, בלי חישוב. סכום אחר — המנוע מחשב את החלופה מחדש."""
        state, snap = ctx.state, ctx.snapshot
        item = next((r for r in state.results if r.option_id == event.option_id), None)
        if item is None:
            return None                        # החלופה כבר לא מוצגת; ההשתתפות נשמרה לפעם הבאה
        if results_stale(state):
            # the result on record predates a change: no before/after from it. The amount is kept
            # (confirmed_copays) and used by the recalculation that follows.
            return rep_copay_text(item.label, amount, event.effective_date)
        used = (item.result.get("alternative") or {}).get("copay")
        if used is not None and Decimal(str(used)) == amount:
            pending_text = f"השתתפות ב-{item.label} לפי הקטלוג — ממתינה לאישור נציג"
            item.result["assumptions"] = [
                confirmed_assumption(item.label, event.effective_date) if a == pending_text else a
                for a in item.result.get("assumptions", [])]
            item.result["copay_pending"] = False
            item.result["copay_source"] = "representative"
            item.result["copay_effective_date"] = event.effective_date.isoformat() if event.effective_date else None
            item.result.pop("employee_stated_copay", None)     # answered: the representative's amount is the one
            return rep_confirmed_text(item.label, event.effective_date, amount, item.result.get("delta_display"))
        option = next((o for o in available_options(snap, state) if o.id == event.option_id), None)
        if option is None:
            return None
        employee, current, occupation = engine_inputs(snap, state)
        alternative = option_state(option, snap.extensions.copay_policy, amount)
        result = _with_assumptions(evaluate(employee, current, alternative, snap.calc, occupation), state,
                                   option.id, option.label)
        inputs = {"employee": employee.model_dump(mode="json"), "current": current.model_dump(mode="json"),
                  "alternative": alternative.model_dump(mode="json"),
                  "occupation": occupation.model_dump(mode="json") if occupation else None}
        old_delta = item.result.get("delta_display")
        item.run_id = record_run(option.label, inputs, result)
        item.result = dump_option(state, option.id, alternative, result)
        if result.delta_display is None:
            return rep_confirmed_text(item.label, event.effective_date, amount)
        return rep_changed_text(item.label, amount, event.effective_date, old_delta, result.delta_display,
                                catalog=option.car.default_copay if option.car else None,
                                today_net=result.today.net_display if result.today else None,
                                new_net=result.alternative.net_display if result.alternative else None,
                                why=item.result.get("why") or "")

    # ---------- חישוב ----------

    def _calculate(self, ctx: ToolContext, record_run: RecordRun) -> None:
        state, snap = ctx.state, ctx.snapshot
        employee, current, occupation = engine_inputs(snap, state)
        options = {o.id: o for o in available_options(snap, state)}
        policy = snap.extensions.copay_policy
        results: list[OptionResult] = []
        needs_opt_out = False
        required_labels: list[str] = []
        for option_id in state.selected:
            option = options.get(option_id)
            if option is None:
                continue
            alternative, result = evaluate_option(snap, state, option, employee, current, occupation)
            inputs = {
                "employee": employee.model_dump(mode="json"),
                "current": current.model_dump(mode="json"),
                "alternative": alternative.model_dump(mode="json"),
                "occupation": occupation.model_dump(mode="json") if occupation else None,
            }
            run_id = record_run(option.label, inputs, result)
            results.append(OptionResult(option_id=option_id, label=option.label, run_id=run_id,
                                        result=dump_option(state, option_id, alternative, result)))
            needs_opt_out = needs_opt_out or "OCCUPATION_OPT_OUT" in result.missing_fields
            # no catalog amount is always pending (rules.car_state), whatever the policy: asked, never only said (D8)
            pending_copay = getattr(alternative, "copay_status", None) == CopayStatus.CATALOG_DEFAULT_PENDING
            asked = option.id in state.rep_copay_asked and option.id not in state.confirmed_copays
            if option.car is not None and (pending_copay or asked) and result.status != CalcStatus.NOT_ELIGIBLE:
                # השתתפות לפי הקטלוג: הנציג מאשר (CONFIRM, התוצאה מוצגת) או חייב לענות (REP_REQUIRED). A car the
                # catalog has no amount for is not calculated (C2): the representative must answer, the others go on
                self._request_new_car_copay(ctx, option, policy)
                if policy == "REP_REQUIRED" and option.car.default_copay is not None:
                    required_labels.append(option.label)
        state.results = results
        ctx.copay_selection = list(state.selected)
        state.rep_copay_asked = [o for o in state.rep_copay_asked if o in state.selected]   # the rest are cancelled
        state.calculated_for = list(state.selected)
        if required_labels and state.pending_rep is None:
            state.pending_rep = PendingRep(reason=RepReason.NEW_CAR_COPAY, resume_stage=Stage.PRESENTED,
                                           labels=required_labels)
        if needs_opt_out:
            p = state.profile
            # the role as the employee confirmed it, only if it reads as one (an older conversation may hold a
            # sentence saved as a role): the representative never sees free text that is not a job title
            role = p.occupation_text if p.occupation_text and looks_like_title(ctx.snapshot, p.occupation_text) else None
            if p.rep_opt_out_allowed is None:
                self._request(ctx, MissingField.OCCUPATION_OPT_OUT, Mode.REQUIRED, AnswerType.BOOLEAN,
                              "האם עובד בעיסוק הזה רשאי לוותר על רכב?",
                              {"group": p.group, "occupation_text": role})
                reason = RepReason.OCCUPATION_OPT_OUT
            else:   # מותר לוותר, וחסר רק סכום האחזקה
                self._request(ctx, MissingField.MAINTENANCE_AMOUNT, Mode.REQUIRED, AnswerType.MONEY,
                              "מה סכום אחזקת הרכב החודשי לעובד בעיסוק הזה אם יוותר על הרכב?",
                              {"group": p.group, "occupation_text": role})
                reason = RepReason.MAINTENANCE_AMOUNT
            if state.pending_rep is None:
                state.pending_rep = PendingRep(reason=reason, resume_stage=Stage.PRESENTED)

    # ---------- ניסוח ----------

    # the claim verifier's own cap. One flash-lite call takes 1.7-1.9 s with this prompt: at 1.2 s the verifier never
    # finished, the turn waited for its deadline and the reply lost its sentences (llm:trimmed). Live QA 22/10.
    VERIFY_BUDGET_S = 2.5
    MIN_VERIFY_S = 0.4          # less than this left in the turn: no verifier, only template-safe sentences
    MIN_RETRY_S = 1.5           # less than this left: no retry (the trimmed reply or the template)

    def _guarded(self, cand: Candidate, draft: str, ctx: ToolContext, history: Sequence[ChatMessage], text: str,
                 deadline: Optional[float], conflicts: Sequence[str],
                 must_answer: bool = False, check_repeat: bool = False) -> tuple[str, str, bool, str]:
        """Guards on the LLM's reply → one retry with the violations as feedback → the reply without the
        offending sentences, if it still stands → the template. Returns (reply, source, guard_blocked, say)."""
        if not cand.text:
            return draft, "template", False, "empty"
        state, snap = ctx.state, ctx.snapshot
        if (state.pending_ambiguity is not None or state.pending_clarification is not None
                or state.pending_confirmation is not None or state.pending_occupation is not None
                or ctx.range_problems):    # a rejected value ("34% גבוה מהטווח…"): the template names it
            # code holds a question of its own (no car or copay 0? the car today or an alternative? 45 or 45,000?
            # this role?): the exact question, with its buttons. A held value is never left unasked: the model
            # once asked about commissions while the salary waited for confirmation (live QA 22/10)
            return draft, "template", False, "pending"
        waiting_answer = any(x is not None for x in (state.pending_confirmation, state.pending_occupation,
                                                     state.pending_clarification, state.pending_ambiguity,
                                                     state.pending_rep_question))
        offer = state.unanswered_question is not None
        fields = [] if (waiting_answer or offer) else list(current_group(state)[1])
        allowed = self._allowed_numbers(ctx, history, text, cand, draft)
        p = state.profile
        closed = [f for f in _CLOSED_FIELDS if getattr(p, f) is not None and f not in fields] if fields else []
        kwargs = dict(snapshot=snap, allowed=allowed, tools_called=cand.tools_called, fields=fields,
                      offer_rep=offer, waiting_answer=waiting_answer, conflicts=conflicts, must_answer=must_answer,
                      one_field=state.pace == "one_at_a_time", closed=closed)

        bots = [m.content for m in history if m.role == "assistant"][-2:]
        previous = [ask_lines(m) for m in bots if ask_lines(m)]      # question lines of the last two bot messages

        def judge(raw: str) -> tuple[Check, list[Violation]]:
            result = check(raw, **kwargs)
            unsupported = self._claims(raw, state, snap, deadline)
            again = ([Violation("repeat")] if check_repeat and ask_lines(result.text) in previous else [])
            return result, [*result.violations, *again, *(Violation("claim", s) for s in unsupported)]

        raw = cand.text
        first, violations = judge(raw)
        if not violations:
            return first.text, "llm", False, "llm"
        left = None if deadline is None else deadline - self.llm.clock()
        retry = self._retry(cand, violations, deadline) if left is None or left >= self.MIN_RETRY_S else ""
        if retry:
            second, again = judge(retry)
            if not again:
                return second.text, "llm", False, "llm:retry"
            raw, violations = retry, again
        if all(v.sentence for v in violations):
            trimmed = drop(raw, [v.sentence for v in violations])      # on the tagged text
            result = check(trimmed, **kwargs) if trimmed else None
            if result is not None and result.ok:
                return result.text, "llm", False, "llm:trimmed"
        kinds = ",".join(sorted({v.kind for v in violations}))
        log.warning("llm reply rejected (%s); template used", kinds)
        return draft, "template", any(v.kind == "number" for v in violations), f"guard:{kinds}"

    def _claims(self, raw: str, state: ConversationState, snap: RulesSnapshot,
                deadline: Optional[float]) -> list[str]:
        """The claim verifier on the sentences beyond ack / question. Out of time: only template-safe
        sentences pass (those tagged with an FAQ entry, or [[catalog]] on a sentence naming a catalog car)."""
        items = content_sentences(raw)
        if not items:
            return []
        left = None if deadline is None else deadline - self.llm.clock()
        if left is not None and left < self.MIN_VERIFY_S:
            safe = {TAG_RE.sub("", s).strip() for s in sentences(raw) if "[[faq:" in s or "[[catalog]]" in s}
            return [s for s in items if s not in safe]
        cap = self.llm.clock() + self.VERIFY_BUDGET_S
        unsupported = verify(self.llm, f"{render_faq(snap)}\n\n{render_catalog(snap)}", state_block(state, snap), items,
                             cap if deadline is None else min(cap, deadline))
        if unsupported is None:          # the verifier failed or ran out of time: template-safe only
            safe = {TAG_RE.sub("", s).strip() for s in sentences(raw) if "[[faq:" in s or "[[catalog]]" in s}
            return [s for s in items if s not in safe]
        return unsupported

    def _retry(self, cand: Candidate, violations: Sequence[Violation], deadline: Optional[float]) -> str:
        feedback = ("Your reply broke these rules:\n" + "\n".join(f"- {v}" for v in violations)
                    + "\nRewrite the whole reply in Hebrew, fixing only that. Keep the source tags. "
                      "Return only the reply.")
        try:
            response = self.llm.complete([*cand.messages, ChatMessage("assistant", cand.text),
                                          ChatMessage("user", feedback)], deadline=deadline)
        except LLMError:
            return ""
        return response.text.strip()

    @staticmethod
    def _allowed_numbers(ctx: ToolContext, history: Sequence[ChatMessage], text: str, cand: Candidate,
                         draft: str) -> set[Decimal]:
        """Numbers the reply may contain: STATE, this turn's tool results, the FAQ, the catalog names and the
        employee's own messages (and the template of this turn, which code built from the same data)."""
        snap = ctx.snapshot
        # numbers of a result that is out of date are not allowed in a reply: they are not current
        dump = ctx.state.model_dump(mode="json", exclude={"results"} if results_stale(ctx.state) else None)
        sources: list[Any] = [dump, cand.tool_results, draft, text,
                              [m.content for m in history if m.role == "user"],
                              [e.answer for e in snap.faq.entries], [c.label for c in snap.catalog], MAX_OPTIONS,
                              len([o for o in available_options(snap, ctx.state) if o.car])]
        return set().union(*(collect_numbers(s) for s in sources))
    # ---------- a question the FAQ does not cover → the transport representative ----------

    def _prepare_rep_question(self, ctx: ToolContext) -> TurnOutcome:
        """"לשאול את נציג התחבורה": the question is rewritten without amounts or personal numbers, checked
        in code, and shown to the employee before anything is sent."""
        state, snap = ctx.state, ctx.snapshot
        question, state.unanswered_question = state.unanswered_question, None
        known = known_question_line(state, question) if question else None
        if known is not None:             # already asked: its status or the answer, never a second question
            plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[known])
            return TurnOutcome(reply=plan.text(), source="template", path="choice")
        if question and not rep_scope_ok(snap, question):
            # checked again in code, whatever offered it: not a question for the transport representative
            plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[snap.faq.redirect])
            return TurnOutcome(reply=plan.text(), source="template", path="choice")
        state.pending_rep_key = general_question_id(question) if question else None
        text = self._rep_rephrase(snap, question) if question else None
        if text is None:
            line = "כדי לשמור על הפרטיות, אשלח לנציג רק שאלה בלי סכומים ובלי מספרים אישיים. כתבו אותה שוב כך."
            plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[line])
        else:
            state.pending_rep_question = text
            plan = plan_reply(state, snap, understood=True, just_calculated=False)
        return TurnOutcome(reply=plan.text(), source="template", path="choice")

    def _rep_rephrase(self, snapshot: RulesSnapshot, question: str) -> Optional[str]:
        def snap_ok(text: str) -> bool:      # the rewrite, too, stays a question about the arrangements (P0-A)
            return rep_ok(snapshot, text) and rep_scope_ok(snapshot, text)

        if self.llm.available:
            try:
                response = self.llm.complete([ChatMessage("system", REP_QUESTION_PROMPT),
                                              ChatMessage("user", question)], deadline=self.llm.turn_deadline())
                text = " ".join(response.text.split())[:300]
                if text and snap_ok(text):
                    return text
            except LLMError:
                pass
        text = " ".join(question.split())[:300]
        return text if snap_ok(text) else None

    def _rep_question_decision(self, ctx: ToolContext, send: bool, record_run: RecordRun) -> TurnOutcome:
        state, snap = ctx.state, ctx.snapshot
        text, state.pending_rep_question = state.pending_rep_question, None
        key, state.pending_rep_key = state.pending_rep_key, None
        if send and text and not rep_scope_ok(snap, text):
            line = snap.faq.redirect      # never opened, whatever was shown: the gate holds at the send too (P0-A)
        elif send and text and rep_ok(snap, text):
            known = known_question_line(state, text)
            if known is not None:         # the same question (as sent) is already with the representative
                line = known
            else:
                # a stable id: the open-question index on the server keeps one, even for a repeated send
                option_id = key or general_question_id(text)
                p, c = state.profile, state.current
                car = rep_car_label(c, ctx.snapshot) if c.source in ("catalog", "tax", "stated") else None
                self._request(ctx, MissingField.GENERAL_QUESTION, Mode.CONFIRM, AnswerType.TEXT, text,
                              {"group": p.group, "car_label": car}, option_id=option_id)
                state.general_questions[option_id] = GeneralQuestion(text=text)
                line = "שלחתי את השאלה לנציג התחבורה. התשובה תגיע לכאן, ובינתיים אפשר להמשיך."
        else:
            line = "בסדר, לא שלחתי."
        plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[line])
        return TurnOutcome(reply=plan.text(), source="template", path="choice",
                           question_requests=ctx.question_requests)


def general_question_id(text: str) -> str:
    """The option_id of a question to the representative: the same words (case, spaces and punctuation aside)
    give the same id, so a repeated question is recognised, never sent twice (QA 22/10)."""
    norm = " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    return "gq-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def known_question_line(state: ConversationState, text: str) -> Optional[str]:
    """A question already sent (asked again, after a reload or in the same words as sent): the answer when it
    came, otherwise that it is still with the representative. None for a new question."""
    qid = general_question_id(text)
    asked = state.general_questions.get(qid) or next(
        (q for q in state.general_questions.values() if general_question_id(q.text) == qid), None)
    if asked is None:
        return None
    if asked.answer is not None:
        return f"נציג התחבורה כבר ענה על השאלה הזו:\n{asked.answer}"
    return "כבר שאלתי את נציג התחבורה את השאלה הזו, והתשובה תגיע לכאן. אין צורך לשאול שוב."


def rep_ok(snapshot: RulesSnapshot, text: str) -> bool:
    """A car name may hold digits ("אטו 3", "MG4"): only those and model years may stay."""
    cleaned = text
    for car in snapshot.catalog:
        for name in sorted(catalog_names_of(car), key=len, reverse=True):
            cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
    return bool(text.strip()) and rep_text_ok(cleaned)


