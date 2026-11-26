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
from . import value_choice
from .fallback import (ZERO_ALLOWED, ParserPlan, _profile_value_correction_plan, asks_base, car_roles, correction_plan,
                       drop_replaced_value, fallback_plan, named_cars, none_plan, read_value, recorded_text)
from .free_text import looks_like_title
from .guard import collect_numbers, violations
from .llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from .grounding import (grounded_number, group_named, occupation_named, option_named, said_numbers,
                        without_alternatives, zero_grounded, bare_zero_answer)
from .parsing import (YEAR_RE, copay_unknown_span, has_all_scope, is_unknown, mask_ages, match_drive, match_group, match_occupation, no_car_span, sounds_like,
                      occupation_match, parse_copay, read_profile, redact_id_numbers, said_approximately)
from .guards import (FIELD_WORDS, STALE_WORDS, SINGULAR_RE, TAG_RE, Check, Violation, answer_part, ask_lines, check,
                     check_answer, content_sentences, drop,
                     has_statement, sentences, strip_tags, ui_violations, verify)
from .turn import LLM_TOOLS, Candidate, parser_hint, render_catalog, render_faq, run_rounds, state_block, system_message, user_message
from .comparison import facts_block, pair_facts, pick_pair
from . import actions, data_facts
from .option_map import build_option_map, dump_option, dump_result as _dump, evaluate_option, map_reply
from .option_map import with_assumptions as _with_assumptions
from .understanding import (ELIGIBLE_CONTEXT_TURNS, asks_estimate, ELIGIBLE_TOPIC, ELIGIBLE_UNCLEAR, NO_CAR_OR_ZERO,
                            NOT_IN_CATALOG, Understanding, answer_like, asks_attribute, rep_scope_ok,
                            meaningful, says_all_words, says_phrase, understand)
from .stage import (
    PendingValueChoice,
    allowed_tools,
    current_group,
    current_unknown,
    MAX_OPTIONS,
    CarClarification,
    ConfirmedCopay,
    ConversationState,
    GeneralQuestion,
    OptionResult,
    PendingConfirmation,
    PendingRep,
    RepCopayOffer,
    ReplyContext,
    RepReason,
    Stage,
    blocking_rep_reason,
    map_ready,
    missing_current_car,
    missing_profile,
    open_profile,
    unknown_missing,
    next_stage,
    results_stale,
    ARG_OF,
    PROFILE_ARGS,
    profile_args,
    Q2_FIELDS,
)
from .templates import (LINK_LABELS, NONE_CHOICES, aside_follow_up,
    options_list_text,
    today_cars_text,
    ALL_AT_ONCE,
    NEXT_AFTER_RESULTS,
    UNANSWERED,
    CHOOSE_TO_COMPARE,
    COPAY_WITH_REP,
    PAYSLIP_OFFER,
    PAYSLIP_REPLACE_OFFER,
    CORRECTION_ASK,
    CORRECTION_CARS,
    correction_field_ask,
    PAYSLIP_UNAVAILABLE,
    copay_with_rep_text,
    catalog_copay_hint,
    ENGINE_OFFLINE,
    compare_needs_text,
    unknown_ack_text,
    call_to_action,
    money,
    plan_reply,
    ReplyPlan,
    GREETING,
    RANK_DEFERRED,
    REP_DEFERRED,
    chosen_options_text,
    COMPARE_WAITS,
    missing_names,
    confirm_group_text,
    confirm_car_text,
    SAVED_STARTS,
    eligible_text,
    _VALUE_LABELS,
    _show,
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
    rep_copay_applied_text,
    rep_copay_offer_text,
    rep_current_text,
    rep_maintenance_text,
    rep_today_maintenance_text,
    rep_note_text,
    rep_request_text,
    rep_copay_text,
    rep_group_text,
    copay_rep_request_text,
    results_explain_text,
    results_reason_text,
    resume_question,
    stated_copay_text,
    group_conflict_text,
    form_saved_text,
    rep_opt_out_text,
    results_text,
    held_fix_text,
)
from .tools import (
    available_options,
    invalidate_results,
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
    # the rep-question endpoint (unified path, Package A): what the write phase reports back to the employee.
    # Filled only by the rep_question handler.
    rep_question_result: Optional["RepQuestionResult"] = None
    # a representative's answer only: the advisor's own next step (the flow question, the results, the map), stored
    # as a message of its own so it never reads as the representative asking it
    follow_up: str = ""
    # the turn decision the reply was composed from (reply contract, 16/11; templates.ReplyPlan.decision)
    decision: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # every reply, from any path: no empty opening line (live Gemini 15/11: a sentence the claim check removed
        # left "\nמה שכר הבסיס…"), and at most one empty line between paragraphs
        self.reply = tidy_reply(self.reply)


_BLANK_RUN_RE = re.compile(r"\n[ \t]*(?:\n[ \t]*)+")


def tidy_reply(text: str) -> str:
    """Leading/trailing blank lines dropped, and a run of empty lines kept as one empty line (the layout of the reply's
    blocks stays: one empty line between them)."""
    return _BLANK_RUN_RE.sub("\n\n", (text or "").strip())


@dataclass
class RepQuestionResult:
    """The outcome of an employee-initiated rep question, for the API to return. status says whether a new
    question was created or an open one already existed; missing_field distinguishes an informational question
    (GENERAL_QUESTION) from a structured copay verification (NEW_CAR_COPAY), for internal use only."""

    status: str                        # "created" | "exists" | "invalid"
    missing_field: str = ""            # "GENERAL_QUESTION" | "NEW_CAR_COPAY" (internal; never shown to the employee)
    question_id: str = ""
    option_id: Optional[str] = None
    car_label: Optional[str] = None
    catalog_amount: Optional[str] = None
    question_text: Optional[str] = None   # the employee's own wording (never the representative's structured text)
    message: str = ""                     # status "invalid": why, in Hebrew, for the employee


# the employee's own question inside a structured copay question: the representative reads the whole text, the
# employee is shown only what follows this mark (employee_wording)
EMPLOYEE_QUESTION_MARK = "\nשאלת העובד: "


def employee_wording(missing_field: MissingField, question_text: str) -> Optional[str]:
    """What the employee wrote, for the employee's screen: the part after the mark in a copay question, the whole
    text of a general question (it is the employee's own), None for a question code opened by itself."""
    if EMPLOYEE_QUESTION_MARK in question_text:
        return question_text.split(EMPLOYEE_QUESTION_MARK, 1)[1]
    return question_text if missing_field == MissingField.GENERAL_QUESTION else None


def cars_named(snapshot: RulesSnapshot, text: str) -> list[Any]:
    """The catalog cars a free text names, once each; a name inside a longer one named too ("COROLLA" within
    "COROLLA CROSS") is the longer car only."""
    found = list({c.catalog_code: c for c in named_cars(text, snapshot.catalog)}.values())
    return [c for c in found
            if not any(o is not c and c.model_name != o.model_name and c.model_name in o.model_name for o in found)]


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


_YES = {"כן", "נכון", "בדיוק", "אכן", "yes", "לאשר", "אשר", "מאשר", "מאשרת", "מאשרים", "מאושר", "בטח", "ברור", "כמובן"}
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
        for f in (pending.field, *pending.also):
            if (pending.unusual or pending.scale) and f not in state.confirmed_unusual:
                state.confirmed_unusual.append(f)   # the chosen amount is saved, not asked again
        args = {pending.field: str(amount), **{f: str(v) for f, v in pending.also.items()},   # held together (1/11)
                "approximate_fields": [pending.field] if pending.approximate else []}
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
    if (pending.value == 0 and snapshot is not None and not others and ws[0] not in ("לתקן", "תקן")
            and bare_zero_answer(text, snapshot.extensions.bare_zero_negatives, snapshot.extensions.bare_zero_words)
            and (len(ws) > 1 or ws[0] not in _NO)):
        # "לאשר פנסיה של 0%?" → "לא מופרש", "באמת אין", "אין בכלל": the zero said again confirms it; a bare "לא" stays
        # "to fix" (live Gemini 1/11: "לא מופרש" dropped the held 0% and asked the pension again)
        return save(pending.value, "confirmed"), ""
    if ws[0] in _NO:
        state.pending_confirmation = None
        if others:
            return None                              # "לא, 50 אלף": a new value, read as usual
        if pending.unusual:
            # "לתקן": the same field is asked again, and it is the only question of the reply
            return ParserPlan(complete=True, notice=held_fix_text(pending), direct=True,
                              asks=None if pending.also else pending.field), rest
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


# save_profile argument → the FAQ entry that explains it (the entries are named after the fields)
_FAQ_OF_ARG = {"base_salary": "base", "children_under_23": "children"}


def _cars_text(state: ConversationState, snapshot: RulesSnapshot, unsure: bool = False, ask: bool = True) -> str:
    """The real cars for the question on screen (1/11): the alternatives once today's car is known (the eligible ones,
    numbered like the selection card), else the whole catalog to find today's car in. `ask`: the template's own
    question line; with the model, its reply asks."""
    if state.current.source is not None and available_options(snapshot, state):
        return options_list_text(state, snapshot, ask)
    return today_cars_text(snapshot, unsure, ask)


def _cars_list(state: ConversationState, snapshot: RulesSnapshot, text: str, und: Understanding) -> Optional[str]:
    """Without the model only (1/11): an unambiguous request to see the cars ("תציג לי אפשרויות", the words in
    faq.show_list_*) or not knowing today's model while it is asked. With the model, the model decides (show_cars).
    None when a ranking, the map, a payslip or a car named is in the message (read as before)."""
    if und.rank or und.map_all or und.payslip_request or named_cars(text, snapshot.catalog):
        return None
    fields = current_group(state)[1]
    unsure = is_unknown(text) and not copay_unknown_span(text)
    if "car" in fields and state.current.source is None and (und.show_list or unsure):
        return _cars_text(state, snapshot, unsure)
    if "options" in fields and state.current.source is not None and und.show_list:
        return _cars_text(state, snapshot)
    return None


def _understand_tools(state: ConversationState) -> tuple[str, ...]:
    """The tools the LLM may call: everything said is saved whatever the stage (the stage is derived from
    the data), except while calculating or waiting for the representative."""
    if next_stage(state) in (Stage.CALCULATING, Stage.WAITING_FOR_REP):
        return ()
    return LLM_TOOLS


# the other contribution: "כמו בפנסיה" is the pension's saved percentage, said by the employee before
_OTHER_CONTRIBUTION = {"pension_percent": "study_fund_percent", "study_fund_percent": "pension_percent"}


def _same(a: Any, b: Any) -> bool:
    try:
        return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
    except Exception:  # noqa: BLE001 — not a number: compare as text ("ב")
        return str(a).strip() == str(b).strip()


def _given_earlier(state: ConversationState, key: str, value: Any) -> bool:
    """Grounding for a value the message does not write (15/11): the employee gave it earlier in this conversation —
    the value this field held before its last change ("עזבי, התכוונתי לקבוצה הקודמת"), or for a contribution the other
    contribution as saved ("כמו בפנסיה"). Gemini reads what the words mean; the value itself is the employee's, never
    the model's, and it still goes through save_profile's gates (ranges, the occupations table)."""
    if _same(state.previous_values.get(key), value):
        return True
    other = _OTHER_CONTRIBUTION.get(key)
    return bool(other) and _same(profile_args(state.profile).get(other), value)


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
BRIEF_ON = "בסדר, אכתוב קצר ופשוט."


def _pace_change(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Optional[str]:
    """"שאלה אחת כל פעם" / "הכול ביחד" / "יותר קצר" (cues in extensions.yaml): how the employee wants to be asked,
    stored in the state and kept across stages until the employee changes it. Returns the acknowledgement, or None."""
    ext = snapshot.extensions
    acks: list[str] = []
    # whole words only: "לאטו 3" (a catalog car) is not "לאט" (QA 22/10)
    if state.pace == "one_at_a_time" and says_phrase(text, ext.pace_reset_cues):
        state.pace = "all_at_once"      # "אפשר הכל ביחד": several at once, never narrowed to one again (16/11)
        acks.append(PACE_NORMAL)
    elif state.pace != "one_at_a_time" and (says_phrase(text, ext.pace_cues)
                                            or says_all_words(text, ext.pace_word_sets)    # any word order (30/10)
                                            # by features: "תשאל" + "אחת" / "בנפרד" / "לאט" / "פחות", anywhere
                                            or says_all_words(text, [[v, m] for v in ext.pace_ask_verbs
                                                                     for m in ext.pace_markers])):
        state.pace = "one_at_a_time"
        acks.append(PACE_ONE)
    if not state.brief and says_phrase(text, ext.brief_cues):
        state.brief = True
        acks.append(BRIEF_ON)
    return " ".join(acks) or None


def _turn(options: Sequence[str], told: int) -> str:
    """The next of several wordings: the same line never twice in a row."""
    return options[told % len(options)] if options else ""


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


_INTENT_FLAGS = ("about_results", "rank", "compare", "map_all", "show_list", "payslip_request", "all_at_once", "audit",
                 "help", "complaint", "redirect", "rep_request")


def _intents(und: Understanding, saved: bool) -> list[str]:
    """What the message was read as, for the turn decision (from the turn's Understanding; nothing read again)."""
    out = ["values"] if saved else []
    out += [f"explain:{i}" for i in und.faq_ids] + (["explain:topic"] if und.topic_lines else [])
    out += [f for f in _INTENT_FLAGS if getattr(und, f, None)]
    out += (["question"] if und.unanswered else []) + (["eligible"] if und.eligible is not None else [])
    out += (["catalog"] if und.catalog else []) + ([f"social:{und.social}"] if und.social else [])
    out += (["correction"] if und.correction is not None else [])
    return out


def calc_missing(state: ConversationState) -> list[str]:
    """What a calculation still needs, by field (missing, said to be unknown, today's car values not known)."""
    p, c = state.profile, state.current
    return [*open_profile(p), *missing_current_car(c), *unknown_missing(p), *current_unknown(c)]


def _owes_explanation(und: Understanding) -> bool:
    """The turn owes an answer the model may write (reply contract, 16/11): a term or topic explained, a "why" about the
    results on screen (from the engine's comparison facts, stage 2), or a question no code fact answers."""
    return bool(und.faq_ids or und.topic_lines or und.unanswered or und.about_results)


def _results_focus(state: ConversationState, snapshot: RulesSnapshot, und: Understanding, text: str) -> list[str]:
    """The results a question about them points at: named (also in other letters or with prefix letters, "שהדולפין"), or
    by their place on the list ("הראשון"). Empty when it points at none."""
    place = value_choice.ordinal_in(text)
    by_code = {c.catalog_code: c for c in snapshot.catalog}
    sounded = [r.option_id for r in state.results if r.option_id in by_code
               and any(sounds_like(text, n) for n in catalog_names_of(by_code[r.option_id]))]
    # both readings together, in the results' order: a name with prefix letters ("מהקונה") may be read by one only
    named = [r.option_id for r in state.results if r.option_id in {*und.result_focus, *sounded}]
    return (named
            or ([state.results[place].option_id]
                if place is not None and -len(state.results) <= place < len(state.results) else []))


PASS_OVER_LIMIT = 2


def _pass_over(state: ConversationState, before: "_Before") -> str:
    """A salary field named in the question on screen (state.asked_line, the words the employee saw), still missing
    while the answer gave other values: counted per field. The second time it is postponed (deferred_fields) until the
    calculation needs it, and said; never assumed, never 0. Returns the acknowledgement, or ""."""
    changed = before.changed(state)
    if not state.asked_line or not changed:
        return ""
    # STALE_WORDS: the specific words of each field ("שכר" alone is also in "יש רכיבי שכר נוספים")
    asked = [f for f in _DEFERRABLE if any(w in state.asked_line for w in STALE_WORDS.get(f, ()))]
    p = state.profile
    if any(f in changed for f in asked):
        # part of the question answered ("5" or "פנסיה 6%, אין ילדים" to "פנסיה וקרן השתלמות?"): the employee is on
        # it; only what is still missing is asked next, never skipped (16/11)
        return ""
    named = [f for f in asked if f in Q2_FIELDS and getattr(p, f) is None and f not in p.unknown_fields
             and f not in state.deferred_fields]
    postponed = []
    for f in named:
        state.passed_over[f] = state.passed_over.get(f, 0) + 1
        if state.passed_over[f] >= PASS_OVER_LIMIT:
            state.deferred_fields.append(f)
            postponed.append(f)
    if not postponed:
        return ""
    names = " ו".join([", ".join(_DEFER_NAMES[f] for f in postponed[:-1]), _DEFER_NAMES[postponed[-1]]]).lstrip(" ו")
    return f"בסדר, נחזור ל{names} כשנצטרך את זה לחישוב."


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


TODAY_COPAY_PREFIX = "today:"
TODAY_MAINTENANCE_ID = TODAY_COPAY_PREFIX + "maintenance"
NO_CAR_LABEL = "ללא רכב צמוד"


def today_maintenance_question_id(state: ConversationState) -> str:
    """The option_id of an employee's inquiry about the car-maintenance allowance (no car today, 19/11): one open inquiry
    per conversation; the "today:" prefix tells apply_answer it is the employee's own value (not the opt-out rule's)."""
    return TODAY_MAINTENANCE_ID


def note_payslip_car(state: ConversationState, snapshot: RulesSnapshot, maintenance: Optional[Decimal]) -> None:
    """A payslip the employee reviewed (19/11): its car-maintenance line, when there is one in the conversation's own
    range, is kept as the offer of the no-car question; it is never saved as a value without the employee's choice.
    A payslip without such a line is remembered too, so the question says nothing was found instead of inventing."""
    state.payslip_reviewed = True
    state.payslip_requested = False          # confirmed: the upload leaves the screen until asked for again (19/11)
    low, high = snapshot.extensions.value_ranges.get("current_maintenance", (Decimal(0), Decimal(0)))
    ok = maintenance is not None and Decimal(low) < Decimal(maintenance) <= Decimal(high)
    state.payslip_maintenance = Decimal(maintenance) if ok else None


def today_copay_question_id(state: ConversationState) -> str:
    """The option_id of an employee's inquiry about today's copay (1/11): one open inquiry per car today; the prefix
    tells apply_answer to offer the amount instead of applying it (the old CURRENT_COPAY questions applied it)."""
    return TODAY_COPAY_PREFIX + (state.current.catalog_code or state.current.label or "car")


def bare_value(text: str) -> bool:
    """A message that is only a number ("34", "6%", "בערך 300 ₪"): it answers the question on screen."""
    return bool(_BARE_RE.match(text)) and any(ch.isdigit() for ch in text)


_SIMPLE_MAX_WORDS = 3            # "קבוצה א", "18,000 ₪", "פנסיה 5%", "קורולה 2026"; a sentence goes to the model
_SIMPLE_TOOLS = {"save_profile", "match_current_car", "select_options", "calculate", "rank_options"}


def _simple_answer(text: str, und: Understanding, plan: ParserPlan) -> bool:
    """A short answer that code read in full and nothing else ("18000", "5%", "אין", "קבוצה א", "קורולה 2026"):
    code saves it and the template replies — no model call (31/10: a bare "18000" cost three Gemini calls). Anything
    with language in it goes to the model: a question, a request, a remark, an uncertainty, small talk, a longer
    message. In doubt, the model: only a plan the parser read in full, with values to save, qualifies."""
    if not plan.complete or not plan.calls or plan.clarify is not None or plan.clarify_scope:
        return False
    if "?" in text or len(words(text)) > _SIMPLE_MAX_WORDS or und.anything or und.has_car or und.map_all:
        return False
    return all(c.name in _SIMPLE_TOOLS for c in plan.calls)


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


def _offers_rep(reply: str) -> bool:
    """The reply's asking line offers to ask the representative ("רוצים שאשאל את נציג התחבורה?")."""
    lines = [ln for ln in reply.splitlines() if ln.strip()]
    last = next((ln for ln in reversed(lines) if "?" in ln), "")
    return "נציג" in last and any(w in last for w in ("אשאל", "לשאול", "אפנה", "לפנות", "אעביר", "להעביר"))


_GROUP_ANSWER_RE = re.compile(r"\s*(?:קבוצה\s*)?([א-ד])['׳\"]?\s*\.?\s*")


def _correction_values(state: ConversationState, snapshot: RulesSnapshot, arg: str, text: str) -> Optional[dict[str, Any]]:
    """The new value for the field a correction asked about (1/11): a bare value ("20000", "6%", "קבוצה ב"); None for
    anything else, which is then read as usual (a question, another value, a change of mind)."""
    if arg == "group":
        m = _GROUP_ANSWER_RE.fullmatch(text)
        return {"eligibility_group": m.group(1)} if m and m.group(1) in snapshot.calc.group_order else None
    if len(words(text)) > 4 or "?" in text:
        return None
    read = read_value(text, snapshot.extensions.answer_fillers)
    if read is None:
        return None
    value = read[0]
    if arg == "children_under_23":
        if Decimal(value) != int(Decimal(value)):
            return None
        value = int(Decimal(value))
    return {arg: value}


def _form_values(state: ConversationState) -> dict[str, Any]:
    """The profile values a form can change, by save_profile argument name."""
    return profile_args(state.profile)


_CAR_ACK_LABELS = (("copay", "השתתפות"), ("use_value", "שווי שימוש"), ("maintenance", "אחזקת רכב"))
# an acknowledged field → its key in templates._VALUE_LABELS (how the value it replaced is shown: "18,500 ₪", "6%")
_ACK_VALUE_KEYS = {**{f: _HIDDEN_ARGS.get(f, "base_salary" if f == "base" else f) for f in _ACK_FIELDS},
                   "copay": "current_copay", "use_value": "current_use_value", "maintenance": "current_maintenance"}


def _ack_parts(changed: dict[str, Any], group: Optional[str]) -> list[str]:
    parts = [f"קבוצה {group}"] if group else []
    profile = {_HIDDEN_ARGS.get(f, "base_salary" if f == "base" else f): v for f, v in changed.items()
               if f in _ACK_FIELDS}
    if profile:
        parts.append(recorded_text(profile))
    for f, label in _CAR_ACK_LABELS:
        if f in changed:
            parts.append(f"{label} {money(changed[f])}")
    return parts


def ack_text(changed: dict[str, Any], group: Optional[str] = None, previous: Optional[dict[str, Any]] = None,
             group_before: Optional[str] = None) -> str:
    """"רשמתי: קבוצה ג, שכר בסיס 18,500 ₪, פנסיה 6%, השתתפות 300 ₪." — every value saved this turn,
    from the state the tools wrote (never from what the LLM said). A value that replaced one saved before is said
    with both (15/11): "עדכנתי: שכר בסיס 20,000 ₪ במקום 18,500 ₪." — whichever path saved it (the chat, the form,
    the model), so the employee sees what changed, once, and only after it was really saved."""
    previous = previous or {}
    replaced = {f: v for f, v in changed.items() if previous.get(f) is not None}
    new = {f: v for f, v in changed.items() if f not in replaced}
    lines = []
    updates = ([f"קבוצה {group} במקום {group_before}"] if group and group_before and group != group_before else [])
    for f, v in replaced.items():
        now = _ack_parts({f: v}, None)
        key = _ACK_VALUE_KEYS.get(f)
        if now and key:
            updates.append(f"{now[0]} במקום {_show(key, previous[f])}")
    if updates:
        lines.append(f"עדכנתי: {', '.join(updates)}.")
    added = _ack_parts(new, None if (group and group_before and group != group_before) else group)
    if added:
        lines.append(f"רשמתי: {', '.join(added)}.")
    return "\n".join(lines)


def _said_in(reply: str, field_name: str, value: Any) -> bool:
    """Does the model's reply name this saved value (14/11)? Its figures (as digits, commas ignored), or, for a value
    with none (a zero said as "אין עמלות"), the field's own word. One value named never stands for the others."""
    line = ack_text({field_name: value}).removeprefix("רשמתי: ").rstrip(".")
    plain = reply.replace(",", "")
    figures = [f.replace(",", "") for f in re.findall(r"\d[\d,.]*", line)]
    if figures and all(f.rstrip(".") != "0" for f in figures):
        return all(f.rstrip(".") in plain for f in figures)
    word = line.split()[-1] if line else ""
    return bool(word) and word in reply


def _eligible_line(state: ConversationState, snapshot: RulesSnapshot, und: Understanding) -> str:
    """The open cars (code's count and names), or the short question when the follow-up may be about the net."""
    if und.eligible == ELIGIBLE_UNCLEAR:
        return snapshot.faq.eligible_clarify
    return eligible_text(state, snapshot, und.eligible or "", note=und.family_note)


def _after_acks(reply: str, block: str) -> str:
    """The block after the reply's opening acknowledgements ("רשמתי"/"עדכנתי"/"זיהיתי"): what changed first, then the
    answer that reflects it (16/11)."""
    lines = reply.split("\n")
    n = 0
    while n < len(lines) and (not lines[n].strip() or lines[n].strip().startswith(SAVED_STARTS)):
        n += 1
    return "\n".join([*lines[:n], block, *lines[n:]]).strip()


def _car_named(reply: str, state: ConversationState, snapshot: RulesSnapshot) -> bool:
    """The reply names today's car: its label's model, or a catalog alias of it ("קורולה" for COROLLA)."""
    c = state.current
    names = [(c.label or "").split(" (")[0]]
    car = next((x for x in snapshot.catalog if x.catalog_code == c.catalog_code), None) if c.catalog_code else None
    if car is not None:
        names += [car.model_name, *car.aliases]
    text = reply.lower()
    return any(n and n.lower() in text for n in names)


def unsaid_ack(reply: str, changed: dict[str, Any]) -> str:
    """"רשמתי: …" for what code saved this turn and the reply leaves out (14/11): "רשמתי: השתתפות 300 ₪" in the
    model's words used to stand for everything, and a car or a value saved with it went unsaid."""
    return ack_text({f: v for f, v in changed.items() if not _said_in(reply, f, v)})


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


# save_profile argument → its key in _values (what a turn changed)
_VALUE_KEY = {**PROFILE_ARGS, "current_copay": "copay", "current_use_value": "use_value",
              "current_maintenance": "maintenance"}


def _estimates(state: ConversationState) -> tuple:
    """(field, value) for every saved value the employee said as an estimate."""
    p, c = state.profile, state.current
    values = {**profile_args(p), "current_use_value": c.use_value, "current_copay": c.copay,
              "current_maintenance": c.maintenance}
    return tuple((f, str(values.get(f))) for f in p.approximate_fields if values.get(f) is not None)


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
    approximate: tuple = ()   # (field, value) said as an estimate, before the turn

    @classmethod
    def of(cls, state: ConversationState) -> "_Before":
        p = state.profile
        return cls((p.occupation, p.group), tuple(state.selected), state.rank_requested, blocking_rep_reason(state),
                   _values(state), current_group(state)[0], tuple(p.unknown_fields),
                   tuple(current_unknown(state.current)), _estimates(state))

    def new_estimates(self, state: ConversationState) -> list[str]:
        """save_profile arguments recorded as an estimate this turn."""
        return [f for f, v in _estimates(state) if (f, v) not in self.approximate]

    def plain_changes(self, state: ConversationState) -> dict[str, Any]:
        """This turn's changes, without the values said as an estimate (those get their own line, once)."""
        estimated = {_VALUE_KEY.get(f, f) for f in self.new_estimates(state)}
        return {f: v for f, v in self.changed(state).items() if f not in estimated}

    def estimate_line(self, state: ConversationState) -> str:
        """"סימנתי כהערכה: קרן השתלמות כ-2.5%. …" for a value recorded as an estimate this turn (16/11), from the state:
        the employee knows the result rests on it, and that an exact value replaces it."""
        new = self.new_estimates(state)
        if not new:
            return ""
        values = {**profile_args(state.profile), "current_use_value": state.current.use_value,
                  "current_copay": state.current.copay, "current_maintenance": state.current.maintenance}
        parts = [f"{_VALUE_LABELS[f][0]} כ-{_show(f, values[f])}" for f in new if f in _VALUE_LABELS]
        return (f"סימנתי כהערכה: {', '.join(parts)}. החישוב יסומן כהערכה, וכשתמצאו את הערך המדויק אעדכן אותו."
                if parts else "")

    def changed(self, state: ConversationState) -> dict[str, Any]:
        return {f: v for f, v in _values(state).items() if v is not None and v != self.values.get(f)}

    def new_values(self, state: ConversationState) -> dict[str, Any]:
        """Saved this turn where nothing was saved before ("רשמתי")."""
        return {f: v for f, v in self.changed(state).items() if self.values.get(f) is None}

    def update_line(self, state: ConversationState) -> str:
        """"עדכנתי: קבוצה ג במקום ב, שכר בסיס 20,000 ₪ במקום 18,500 ₪." — only what replaced a value saved before this
        turn, from the state (15/11: the model's "רשמתי: קבוצת זכאות ג." hid the change and the value it replaced)."""
        p = state.profile
        group_before = self.occupation[1]
        group = p.group if (p.group and group_before and not p.occupation and p.group != group_before) else None
        replaced = {f: v for f, v in self.changed(state).items() if self.values.get(f) is not None}
        return ack_text(replaced, group, self.values, group_before) if (replaced or group) else ""

    def confirmations(self, state: ConversationState, snapshot: RulesSnapshot, just_calculated: bool,
                      recorded_said: bool = False) -> list[str]:
        p, lines = state.profile, []
        # every value saved this turn is said once ("רשמתי: …"), never saved silently
        new_group = p.group if (p.group and not p.occupation and (p.occupation, p.group) != self.occupation) else None
        line = ack_text(self.plain_changes(state), new_group, self.values, self.occupation[1])
        if line and not recorded_said:
            lines.append(line)
        unknown = [f for f in p.unknown_fields if f not in self.unknown]
        if unknown:
            lines.append(unknown_ack_text(unknown))
        estimate = self.estimate_line(state)
        if estimate:
            lines.append(estimate)
        conflict = state.group_conflict
        if conflict is not None and not conflict.announced:
            lines.append(group_conflict_text(conflict.stated, conflict.table, conflict.occupation))
            conflict.announced = True
        elif p.occupation and p.group and (p.occupation, p.group) != self.occupation and conflict is None:
            lines.append(confirm_group_text(p.occupation, p.group))
        new_unknown = [f for f in current_unknown(state.current) if f not in self.car_unknown]
        if new_unknown and current_group(state)[0] == "unknown":
            # recorded now and nothing else is left (19/11): the end-of-flow line that follows says what the
            # calculation waits for and every way on — said as a status, never the same question asked again at once
            state.unknown_told = 1
        elif new_unknown:
            lines.append(MAINTENANCE_UNKNOWN_ACK if new_unknown == ["maintenance"] else CURRENT_UNKNOWN_ACK)
        if state.selected and tuple(state.selected) != self.selected and not just_calculated:
            labels = {o.id: o.label for o in available_options(snapshot, state)}
            lines.append(chosen_options_text([labels.get(i, i) for i in state.selected]))
            # the unknown-values question that follows says why there is no net yet, in its own words
            if missing_names(state) and current_group(state)[0] != "unknown":
                lines.append(COMPARE_WAITS)     # no cards yet: said, and the missing values are the question that follows
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
# a typed "לא ידוע" while today's copay choices are open (16/11): what to choose, never skipped to the comparison
# (nothing is saved). The ways to go on: the rep, the amount from the payslip, or "לא ידוע" again (recorded as unknown,
# asked again only when the calculation needs it): never the same choices over and over
COPAY_UNKNOWN_CHOOSE = ("אם אתם לא יודעים את הסכום, אפשר לבחור \"לברר מול נציג\" ואשלח לו את השאלה, "
                        "לכתוב את הסכום כפי שהוא מופיע בתלוש השכר, "
                        "או לכתוב שוב \"לא ידוע\" ונמשיך בינתיים בלי הסכום.")
COPAY_OTHER_ASK = "כמה בדיוק יורד לכם מהמשכורת בכל חודש עבור הרכב של היום? כתבו את הסכום המדויק, למשל: 280."
CURRENT_UNKNOWN_ACK = ("רשמתי שזה לא ידוע, בלי לנחש סכום. הוא מופיע בתלוש השכר החודשי, ואם תדעו רק בערך אפשר לכתוב"
                       " הערכה. נמשיך, ונחזור אליו כשנצטרך אותו לחישוב.")
# no car today, the allowance not known (19/11): the payslip only "if there is one", and the ways it is completed later
MAINTENANCE_UNKNOWN_ACK = ("רשמתי שסכום אחזקת הרכב לא ידוע, בלי לנחש ובלי להציב 0. נמשיך, ונשלים אותו לפני החישוב: "
                           "אם אתם מקבלים אחזקה היא מופיעה ברכיבי השכר בתלוש, ואפשר גם לכתוב הערכה או לברר מול נציג.")
MAINTENANCE_OTHER_ASK = "כמה אחזקת רכב אתם מקבלים בכל חודש? כתבו את הסכום, למשל: 1,500."
MAINTENANCE_WITH_REP = ("רשמתי: את סכום אחזקת הרכב נברר מול נציג התחבורה, והתשובה תגיע לכאן. "
                        "עד אז לא אחשב נטו ולא אציב סכום משלי. אם אתם יודעים בערך, אפשר לכתוב הערכה ואסמן את "
                        "התוצאות כמשוערות.")


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

    @staticmethod
    def _with_cars(ctx: ToolContext, reply: str, from_model: bool) -> str:
        """The model called show_cars: code's list of real cars, then the model's (guarded) reply asking which one. When
        a guard replaced the model's reply, the list's own question and the call to action (never a generic recap)."""
        if not ctx.cars_shown:
            return reply
        if not from_model:
            return f"{_cars_text(ctx.state, ctx.snapshot)}\n\n{reply.splitlines()[-1]}"
        return f"{_cars_text(ctx.state, ctx.snapshot, ask=False)}\n\n{reply}"

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
        state.payslip_offer_now = False          # the bot's offer of the upload belongs to one reply (23/11)
        asked_value, state.pending_value_choice = state.pending_value_choice, None
        none_asked, state.pending_none_choice = state.pending_none_choice, None
        if field_ == "none_choice" and none_asked and ids[0] in none_asked and ids[0] in NONE_CHOICES:
            # what a bare "אין" was about (23/11): only the chosen button's fields are recorded as 0
            return self._finish(ctx, (), label, none_plan(NONE_CHOICES[ids[0]][1]), before, record_run, choice=True)
        if field_ == "value_choice" and asked_value is not None and (
                chosen := value_choice.from_button(asked_value, ids)) is not None:
            return self._value_chosen(ctx, asked_value, chosen, (), label, before, record_run)
        if field_ == "ask_rep":
            if ids[0] == "skip":                     # "להמשיך בלי": back to the flow question
                state.unanswered_question, state.rep_skipped = None, True
                return self._finish(ctx, (), label, ParserPlan(complete=True, direct=True), before, record_run,
                                    choice=True)
            return self._prepare_rep_question(ctx)
        if field_ == "rep_question":
            return self._rep_question_decision(ctx, ids[0] == "send", record_run)
        if field_ == "rep_copay":
            # the employee confirmed the representative's amount for today's copay (1/11). The offer is removed in the
            # same turn, so a second click, a reload or a reopened conversation can never apply it again
            offer = state.rep_copay_offer
            if offer is None or ids[0] != f"apply:{offer.question_id}":
                return TurnOutcome(reply="העדכון הזה כבר בוצע או שאינו זמין יותר.", source="template", path="choice")
            previous = state.current.copay
            state.rep_copay_offer = None
            plan = ParserPlan([ToolCall("save_profile", {"current_copay": str(offer.amount)}, "combined")], True,
                              notice=rep_copay_applied_text(offer.amount, previous), direct=True, acked=True)
            ctx.copay_source = "rep_confirmed"       # the representative's answer, applied by the employee
            return self._finish(ctx, (), label, plan, before, record_run, choice=True)
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
            if field_ == "copay" and ids[0].startswith("catalog:"):
                ctx.copay_source = "catalog_confirmed"   # "זה הסכום שלי": the catalog amount, confirmed (1/11)
            plan = ParserPlan(choice_calls(field_, ids, snapshot, state.payslip_maintenance), True, direct=True)
            if field_ == "copay" and ids == ["other"]:
                # "הסכום שלי שונה" (16/11): the exact amount is asked, in words too (the client opens an amount field)
                plan = replace(plan, notice=COPAY_OTHER_ASK, asks="current_copay")
            if field_ == "copay" and ids == ["rep"]:
                ctx.copay_rep_chosen = True              # the choice itself: the copay is unknown until the rep answers
                # the inquiry about today's copay was sent: the copay stays unknown (16/11) — no net is calculated
                # until the representative's answer is confirmed or the employee writes an estimate (marked as one);
                # never the catalog amount in its place
                state.current.copay_provisional = None
                if "copay" not in state.rep_fields:
                    state.rep_fields.append("copay")      # the explicit status: with the representative (19/11)
                plan = replace(plan, notice=copay_with_rep_text(state.current.copay_provisional), acked=True)
            if field_ == "maintenance" and ids == ["payslip"]:
                ctx.value_source = "payslip"             # the amount read from the payslip, confirmed (19/11)
            if field_ == "maintenance" and ids == ["other"]:
                # "הסכום שונה" / "להזין סכום": the exact amount is asked (the client opens an amount field)
                plan = replace(plan, notice=MAINTENANCE_OTHER_ASK, asks="current_maintenance")
            if field_ == "maintenance" and ids == ["rep"]:
                # the inquiry about the allowance was sent (19/11), the same flow as today's copay: unknown, with the
                # representative, no net until the answer or an estimate; never a default and never 0
                if "maintenance" not in state.rep_fields:
                    state.rep_fields.append("maintenance")
                plan = replace(plan, notice=MAINTENANCE_WITH_REP, acked=True)
        return self._finish(ctx, (), label, plan, before, record_run, choice=True)

    def run_fields(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, get_vehicle: VehicleGetter,
                   state: ConversationState, values: dict[str, Any], label: str, record_run: RecordRun,
                   source: Optional[str] = None) -> TurnOutcome:
        """הטופס המובנה (שדות הפרופיל, כבר נבדקו): נשמרים ישירות בכלי save_profile — בלי הקורא ובלי מודל
        השפה. אחרי תיקון, השלב נגזר מהנתונים: תוצאה קיימת מחושבת מחדש."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state, get_vehicle=get_vehicle,
                          exact_amounts=True)
        before = next_stage(state)
        state.payslip_offer_now = False          # the bot's offer of the upload belongs to one reply (23/11)
        state.pending_none_choice = None         # another answer: the "אין" buttons leave with their question
        previous = {**_form_values(state), "current_copay": state.current.copay,
                    "current_use_value": state.current.use_value, "current_maintenance": state.current.maintenance}
        group_before, occupation_before, car_before = state.profile.group, state.profile.occupation, state.current.label
        ctx.value_source = "payslip" if source == "payslip" else "form"
        # A value held from chat ("פנסיה 0": לאשר?) is older than the form: a form value for the same field
        # replaces it; a held value for another field stays held (never dropped silently).
        held_before = state.pending_confirmation
        state.pending_confirmation = None
        # הטופס הוא פעולה מפורשת של העובד (גם תיקון אחרי תוצאה), לכן לא עובר דרך _run ומגבלת הכלים של
        # השלב; run_tool נשאר השער היחיד לשמירה. השלב נגזר מהנתונים: calculated_for מתאפס ← חישוב מחדש.
        car = values.get("current_car")
        if car is not None:
            # today's car edited on its own ("הנתונים שלי"): the same tool as in the conversation; another car than the
            # one on record replaces it (its copay is asked again unless it is the same car)
            known = state.current.source is not None
            run_tool(ctx, "match_current_car", {"no_car": True} if car == "no_car" else
                     {"vehicle_key": car, **({"reset": True} if known else {})} if str(car).startswith("tax:") else
                     {"model": car, **({"reset": True} if known else {})})
        rest = {k: v for k, v in values.items() if k != "current_car"}
        facts = run_tool(ctx, "save_profile", rest) if rest else {"saved": []}
        if state.pending_confirmation is None and held_before is not None and held_before.field not in values:
            state.pending_confirmation = held_before
        saved = {k: values[k] for k in facts.get("saved", []) if k in values and k in previous}
        pay = {k: v for k, v in saved.items() if not k.startswith("current_")}
        lines = [form_saved_text(previous, pay, source)] if pay else []
        for key, value in saved.items():
            if key.startswith("current_"):       # today's car values: said with their own label ("השתתפות נוכחית")
                label = _VALUE_LABELS[key][0]
                lines.append(f"עדכנתי: {label} {_show(key, value)} במקום {_show(key, previous[key])}."
                             if previous.get(key) is not None else f"רשמתי: {label} {_show(key, value)}.")
        if "eligibility_group" in values and state.profile.group != group_before:
            lines.append(f"עדכנתי: קבוצה {state.profile.group}" + (f" במקום {group_before}." if group_before else "."))
        if "occupation" in values and state.profile.occupation and state.profile.occupation != occupation_before \
                and state.profile.group:
            lines.append(confirm_group_text(state.profile.occupation, state.profile.group))
        if car is not None and state.current.label != car_before:
            # the car it replaced and a copay that stayed from it are said by confirm_car_text, as in the chat
            # (match_current_car marks the carried copay: never taken silently as the new car's own)
            lines.append(confirm_car_text(state) if state.current.source in ("catalog", "tax")
                         else "רשמתי: אין לכם רכב צמוד היום." if state.current.source == "none" else "")
        notice = "\n".join(line for line in lines if line) or (
            "הערכים זהים למה שכבר רשמתי." if values else "")
        plan = ParserPlan([], True, notice=notice, direct=True, acked=True) if notice             else ParserPlan([], True, direct=True)
        return self._finish(ctx, (), label, plan, before, record_run, choice=True)

    def run_turn(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        before = next_stage(state)
        aside = understand(state, snapshot, text).aside
        if aside is not None:
            # 24/11: small talk or off-topic asks nothing of the service: a fixed line and one follow-up line, by code,
            # before anything of the turn changes (the question on screen and its buttons stay, nothing is saved)
            opening = snapshot.faq.small_talk_reply if aside == "small_talk" else snapshot.faq.off_topic_reply
            return TurnOutcome(reply="\n\n".join(x for x in (opening, aside_follow_up(state, snapshot)) if x),
                               source="template", path="aside")
        state.payslip_offer_now = False          # the bot's offer of the upload belongs to one reply (23/11)
        state.pending_none_choice = None         # another answer: the "אין" buttons leave with their question

        # one number for several fields ("2" → "לשניהם"; production 9/11: "לשתיהם" was not understood and both
        # contributions went back to missing): the question held in the state is answered first; a later "בעצם רק
        # לפנסיה" re-applies the choice; a bare number for a question about several fields is asked by code
        asked_value, state.pending_value_choice = state.pending_value_choice, None
        chooser = self.llm if self._llm_led() else None
        if asked_value is not None:
            chosen = value_choice.resolve(chooser, text, asked_value.fields, asked_value.value,
                                          deadline=self.llm.turn_deadline(), question=state.asked_line)
            if chosen is not None:
                return self._value_chosen(ctx, asked_value, chosen, history, text, before, record_run)
            if (again_asked := value_choice.ask_again(text, asked_value)) is not None:
                return self._ask_value_choice(state, again_asked)    # never dropped silently: asked in other words
            if asked_value.asked >= 2 and not value_choice.is_question(text):
                state.pace = "one_at_a_time"          # asked twice: from here one field at a time, each unambiguous
        elif (again := value_choice.correction(state, chooser, text, self.llm.turn_deadline())) is not None:
            return self._value_rechosen(ctx, again, history, text, before, record_run)
        if (held := value_choice.open_for_bare_value(state, snapshot, text)) is not None:
            return self._ask_value_choice(state, held)

        und = understand(state, snapshot, text)
        state.last_topic = und.topic          # read by understand() above; the next message's "זה" refers to it
        # the open-cars context lasts a few turns (a question between does not end it), then expires
        state.eligible_unclear_asked = und.eligible == ELIGIBLE_UNCLEAR
        if und.eligible == "" and not state.family_note_told and state.profile.children is None:
            und = replace(und, family_note=True)      # the family-track note: once, while it may change the answer
            state.family_note_told = True
        state.eligible_turns = (0 if und.eligible is not None
                                else None if state.eligible_turns is None or state.eligible_turns >= ELIGIBLE_CONTEXT_TURNS
                                else state.eligible_turns + 1)
        known = known_question_line(state, und.unanswered[0]) if und.unanswered else None
        if known is not None:
            # a question already sent (1/11): its status, or the answer already given, is the reply; never offered to
            # the representative again (the offer was shown though clicking it would send nothing new)
            und = replace(und, unanswered=und.unanswered[1:], catalog=[*und.catalog, known])
        # a correction asked for in the last reply (1/11): this message may be its new value; asked once only
        correcting, state.correcting_field = state.correcting_field, None
        # the model understands free text; word lists decide only without it (hybrid routing, 1/11)
        cars = None if self._llm_led() else _cars_list(state, snapshot, text, und)
        und = replace(und, show_list=cars is not None)
        if und.unanswered:
            state.last_rep_question = und.unanswered[0]     # kept for "היית צריך לשאול את הנציג" in a later turn
        ctx.question_message = und.question_only or text.strip().endswith("?")
        pace = _pace_change(state, snapshot, text)
        if pace is not None and (len(words(text)) <= 6 or not answer_like(snapshot, und.remainder)):
            # only the request ("שאלה אחת כל פעם", "תעשה לי את זה לאט, שאלה שאלה"): acknowledged, kept in the state,
            # then the next question; a longer message with a value in it goes on to be read
            return self._finish(ctx, history, text, ParserPlan(complete=True, notice=pace, direct=True), before,
                                record_run, und=Understanding(remainder="", resume=True))
        if before == Stage.WAITING_FOR_REP:
            if (restated := self._group_conflict_restated(ctx, text, before, record_run)) is not None:
                return restated
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
                reply = "\n\n".join([*self._answers(snapshot, und, state)[0], *extra, reply])
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
        elif und.rep_request:
            # "אני רוצה לדבר עם נציג": the offer at once, code decides (both modes); no pay question this turn
            return self._rep_requested(ctx, und)
        elif cars is not None:
            # "תציג לי אפשרויות", "לא זוכרת איזה רכב" (1/11): the real catalog list for the question on screen (today's
            # car or the alternatives); code decides in both modes, nothing is saved or assumed
            return self._finish(ctx, history, text, ParserPlan(complete=True, notice=cars, direct=True, acked=True),
                                before, record_run, choice=True, und=replace(und, remainder="", show_list=True))
        elif und.payslip_request:
            # "אני רוצה להעלות תלוש" (1/11): the uploader at once, whatever the flow would ask; code decides (the action
            # is the code's), the reply says what happens to the file. Unavailable here: said plainly.
            # 19/11: offered only when it can be used now; a new payslip over values already given says that every
            # change is shown for approval before anything is replaced
            replacing = state.payslip_reviewed or any(v is not None for v in profile_args(state.profile).values())
            notice = (PAYSLIP_UNAVAILABLE if not actions.payslip_possible(state, snapshot)
                      else PAYSLIP_REPLACE_OFFER if replacing else PAYSLIP_OFFER)
            return self._finish(ctx, history, text, ParserPlan(complete=True, notice=notice, direct=True, acked=True),
                                before, record_run, choice=True, und=replace(und, remainder=""))
        elif correcting and (values := _correction_values(state, snapshot, correcting, text)) is not None:
            # the new value of the field the employee asked to correct: saved like the correction form (the same gate:
            # ranges, unusual values), at any stage; the results and the option map are recalculated
            if correcting in PROFILE_ARGS:
                return self._save_while_waiting(ctx, text, values, before, record_run)
            plan = ParserPlan([ToolCall("save_profile", values, "combined")], True, direct=True)
        elif und.zero_contributions:
            # "אין הפרשות" (1/11): pension and study fund held at 0 together, asked once ("לאשר" saves both, "לתקן"
            # asks them again); the same gate as any held value. Code decides, in both modes
            zero = Decimal(0)
            fields = und.zero_contributions
            state.pending_confirmation = PendingConfirmation(field=fields[0], value=zero, unusual=True,
                                                             also={f: zero for f in fields[1:]})
            return self._finish(ctx, history, text, ParserPlan(complete=True, direct=True), before, record_run,
                                choice=True, und=replace(und, remainder=""))
        elif und.correction is not None:
            # "אני רוצה לתקן נתון" (1/11): code asks what, or the new value of the field named; never the representative
            return self._correction_requested(ctx, history, text, und, before, record_run)
        elif (same_value := value_choice.same_contribution_value(state, text)) is not None:
            field, value = same_value
            plan = ParserPlan([ToolCall("save_profile", {field: str(value)}, "same_value")], True, direct=True)
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
        elif (state.asked_field == "current_copay" and len(words(text)) <= 3 and "?" not in text
              and words(text)[:1] in (["כן"], ["נכון"], ["בדיוק"], ["כן,"])
              and (hint := catalog_copay_hint(state, snapshot)) is not None):
            # "כן" to "בקטלוג רשום 300 ₪… זה הסכום שאתם משלמים?": the employee confirmed the catalog amount (31/10)
            ctx.copay_source = "catalog_confirmed"     # the catalog amount, confirmed by the employee (1/11)
            plan = ParserPlan([ToolCall("save_profile", {"current_copay": str(hint)}, "catalog_confirmed")], True,
                              direct=True)
        elif (state.current.source in ("catalog", "tax", "stated")
              and (said := drop_replaced_value(state, text)) is not None
              and (amount := parse_copay(said)) is not None
              and amount != state.current.copay and "?" not in said and len(re.findall(r"\d[\d,.]*", said)) == 1
              and all(car.catalog_code == state.current.catalog_code for car in named_cars(said, snapshot.catalog))):
            # today's copay changed later in the conversation ("טעיתי, ההשתתפות שלי 220", 31/10): saved as said, at any
            # stage; the results are recalculated. A copay said about another car is a new car's (the branch above).
            # "אני משלמת 400 ולא 300" (15/11): the value it replaces, named after it, is not a second number
            plan = ParserPlan([ToolCall("save_profile", {"current_copay": str(amount)}, "combined")], True,
                              direct=True)
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

    def _correction_requested(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str, und: Understanding,
                              before: Stage, record_run: RecordRun) -> TurnOutcome:
        """A correction of the employee's own data with no value yet (1/11). No field named: what to correct, with the
        form. One field: its new value, with what is recorded now (the next bare value is saved to it). Today's car:
        the car correction ("זה לא הרכב שלי": the model asked again, the copay kept, the results out of date)."""
        state = ctx.state
        fields = und.correction or []
        if fields == ["car"]:
            plan = ParserPlan([ToolCall("match_current_car", {"reset": True}, "combined")], True, direct=True)
            return self._finish(ctx, history, text, plan, before, record_run, choice=True,
                                und=replace(und, correction=None, remainder=""))
        if len(fields) == 1:
            state.correcting_field = fields[0]
            notice = correction_field_ask(state, fields[0])
        else:
            notice = CORRECTION_ASK + (f"\n{CORRECTION_CARS}" if state.results else "")
        return self._finish(ctx, history, text, ParserPlan(complete=True, notice=notice, direct=True, acked=True),
                            before, record_run, choice=True, und=replace(und, remainder=""))

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

    def _group_conflict_restated(self, ctx: ToolContext, text: str, before: Stage,
                                 record_run: RecordRun) -> Optional[TurnOutcome]:
        """The employee restates the group while the representative is asked about a group that contradicts the
        occupations table (C4, 15/11: the conversation stayed blocked after "רגע, אני בקבוצה ג" settled it).
        The table's group: the contradiction is gone — the question to the representative is cancelled and the
        conversation goes on from where it stopped. Another group: the representative is asked again, about it."""
        state = ctx.state
        conflict, pending = state.group_conflict, state.pending_rep
        if conflict is None or pending is None or pending.reason != RepReason.ELIGIBILITY_GROUP:
            return None
        order = ctx.snapshot.calc.group_order
        if not group_named(text, order, FAMILY_GROUP) or (group := match_group(order, text)) is None:
            return None
        if group == conflict.stated:
            return None                                   # the same as before: still the representative's to decide
        ctx.cancel_question_ids.extend(pending.question_ids)
        state.pending_rep = None
        if group == conflict.table:
            state.group_conflict = None
            state.profile.group = group
            notice = (f"עדכנתי: קבוצה {group} במקום {conflict.stated}. זו גם הקבוצה של התפקיד {conflict.occupation} "
                      "בטבלת העיסוקים, ולכן אין צורך בתשובת נציג התחבורה: ביטלתי את השאלה אליו.")
        else:
            notice = (f"עדכנתי: ציינתם קבוצה {group} במקום {conflict.stated}. לפי טבלת העיסוקים התפקיד "
                      f"{conflict.occupation} שייך לקבוצה {conflict.table}, ולכן נציג התחבורה יכריע.")
            state.group_conflict = conflict.model_copy(update={"stated": group, "announced": True})
        invalidate_results(state)
        plan = ParserPlan([], True, notice=notice, direct=True, acked=True)
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

    # ---------- one number, several fields ----------

    @staticmethod
    def _ask_value_choice(state: ConversationState, pending: PendingValueChoice) -> TurnOutcome:
        state.pending_value_choice = pending
        reply = value_choice.question_text(pending)
        state.last_ask = state.asked_line = value_choice.question_line(reply)
        state.asked_field = None
        state.stage = next_stage(state)
        return TurnOutcome(reply=reply, source="template", path="value_choice")

    def _value_chosen(self, ctx: ToolContext, pending: PendingValueChoice, chosen: list[str],
                      history: Sequence[ChatMessage], text: str, before: Stage, record_run: RecordRun) -> TurnOutcome:
        """The number saved to the field(s) the employee chose, through save_profile's gates; "none of them" saves
        nothing and the flow asks the fields again."""
        state = ctx.state
        if not chosen:
            plan = ParserPlan(complete=True, direct=True, acked=True, notice=value_choice.NONE_TEXT)
            return self._finish(ctx, history, text, plan, before, record_run, choice=True, und=Understanding(remainder=""))
        state.last_value_choice = value_choice.resolved(pending, chosen)
        plan = ParserPlan([ToolCall("save_profile", value_choice.save_args(pending.value, chosen), "confirmed")], True,
                          direct=True)
        return self._finish(ctx, history, text, plan, before, record_run, choice=True, und=Understanding(remainder=""))

    def _value_rechosen(self, ctx: ToolContext, chosen: list[str], history: Sequence[ChatMessage], text: str,
                        before: Stage, record_run: RecordRun) -> TurnOutcome:
        """"בעצם רק לפנסיה": the number goes to the new choice; a field dropped from it is cleared only while it still
        holds that number (it is asked again, never left with a value the employee withdrew)."""
        state = ctx.state
        last = state.last_value_choice
        assert last is not None
        cleared = [f for f in last.fields if f not in chosen and getattr(state.profile, PROFILE_ARGS[f]) == last.value]
        for f in cleared:
            setattr(state.profile, PROFILE_ARGS[f], None)
        if cleared:
            invalidate_results(state)
        state.last_value_choice = last.model_copy(update={"chosen": list(chosen)})
        todo = [f for f in chosen if getattr(state.profile, PROFILE_ARGS[f]) != last.value]
        calls = [ToolCall("save_profile", value_choice.save_args(last.value, todo), "combined")] if todo else []
        plan = ParserPlan(calls, True, direct=True, acked=True,
                          notice=value_choice.cleared_text(last.value, chosen, cleared))
        return self._finish(ctx, history, text, plan, before, record_run, choice=True,
                            und=Understanding(remainder="", resume=True))

    def _finish(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str, plan: ParserPlan,
                before: Stage, record_run: RecordRun, choice: bool = False,
                und: Optional[Understanding] = None, extra: Sequence[str] = ()) -> TurnOutcome:
        """UNDERSTAND (parser + one LLM call) → DECIDE (code: save, then a reply plan) → SAY (the LLM
        writes the plan; the template renders it when the LLM is off, fails or breaks a check)."""
        state, snapshot = ctx.state, ctx.snapshot
        und = und or Understanding(remainder=text)
        state.link_now = None               # a link is offered on one reply only (templates._once)
        if und.payslip_request and actions.payslip_possible(state, snapshot):
            state.link_now = "payslip_upload"   # the uploader, on this reply, whatever the flow asks (1/11)
            state.payslip_requested = True       # and on the screen again until a payslip is confirmed (19/11)
        if und.action is not None:
            state.link_now = und.action          # the action this turn is about, on this reply (19/11)
            if und.action == actions.PAYSLIP_UPLOAD:
                state.payslip_requested = True
        if und.correction is not None and actions.payslip_possible(state, snapshot) and (
                not und.correction or any(f in PROFILE_ARGS for f in und.correction)):
            # "אני רוצה לעדכן את נתוני השכר" (19/11): the correction form, and the upload next to it — a new payslip is
            # another way to update the salary details (its review shows every change before anything is replaced)
            state.payslip_requested = True
        if und.correction is not None:
            state.link_now = "correction_form"   # the correction form, on this reply (1/11)
        if und.all_at_once:
            state.pace = "all_at_once"      # "all at once" is an explicit change of pace
            state.link_now = "profile_form"  # the form is the way to answer everything together
        car_before = _car_key(state)
        before_turn = _Before.of(state)
        # a "why" about results on screen compares two of them (stage 2, 16/11): chosen once, before the model reads the
        # message, so its prompt, the claim verifier and code's answer use the same engine facts
        pair = None
        if und.about_results and state.results and state.stage == Stage.PRESENTED and not asks_estimate(snapshot, text):
            focus = _results_focus(state, snapshot, und, text)
            pair = pick_pair(state, focus) if focus else None
        # a question about a value the calculation still needs, or about the calculation while values are missing:
        # what is missing, how the engine uses it, the system's rule (data_facts, 16/11) — never a general "how I work"
        # a question: as the rules read it, or plainly asked ("…מה תעשה?") — data_facts then needs a missing field named
        asks = bool(und.faq_ids or und.topic_lines or und.unanswered or und.help or und.compare or und.missing_data
                    or text.rstrip().endswith("?"))
        concerned = [] if pair is not None else data_facts.concerned(state, snapshot, text, und.faq_ids, asks)
        missing_found = data_facts.facts(state, snapshot, concerned)
        # the claim verifier gets every fact; the model's prompt the same, without the rule line code says itself
        facts = "\n\n".join(x for x in (facts_block(pair), data_facts.facts_block(missing_found)) if x)
        on_screen = [f'"{actions.LABELS[k]}" ({actions.HELP[k].split(".")[0]})' for k in actions.available(state, snapshot)]
        screen_block = ("ACTIONS ON SCREEN (the only buttons you may mention, by this exact label): "
                        + ("; ".join(on_screen) if on_screen else "none"))
        prompt_facts = "\n\n".join(x for x in (facts_block(pair),
                                              data_facts.facts_block(missing_found, for_model=True), screen_block) if x)
        data_before = (dict(profile_args(state.profile)), list(state.selected))
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
        simple = _simple_answer(text, und, plan)       # code saves it, the template replies (no model call)
        phrase_only = False                             # code decided the turn; the model only words it
        parser_decides = plan.complete and (self.mode == "parser_first" or plan.calls == [] or plan.direct or simple)
        if parser_decides or not self.llm.available:
            path = "choice" if choice else "parser" if plan.complete else "templates"
            # a direct answer to the code's own question ("לא" to a confirmation) is understood
            understood = self._execute(ctx, plan.calls) or plan.notice is not None or (plan.direct and plan.complete)
            if plan.clarify_scope:
                # a blanket "לא ידוע" / "אין לי כלום" / "הכל 5" on a grouped question: nothing is saved,
                # and from here the fields are asked one at a time, so each answer is unambiguous.
                state.pace = "one_at_a_time"
            if plan.none_choices:
                state.pending_none_choice = list(plan.none_choices)   # a bare "אין": asked with buttons (23/11)
            if plan.clarify is not None:
                state.pending_clarification = plan.clarify     # "<רכב> הוא הרכב שלכם היום, או חלופה?"
                state.role_asked.append(plan.clarify.catalog_code)
                understood = True
            if (self._llm_led() and not choice and next_stage(state) != Stage.CALCULATING
                    and not simple and not plan.none_choices
                    and (meaningful(und.remainder) or _owes_explanation(und))):
                # saved in code (an answer to the bot's own question, or a message read in full); the model reads
                # what is left of the message and writes the explanation it asks for. A message code read in full
                # that asks nothing needs no model call: the turn decision is code's (reply contract, 16/11)
                try:
                    _, candidate = self._llm_turn(ctx, history, text, plan, deadline, {}, conflicts, und,
                                                  applied=parser_hint(plan.calls) or "nothing (the message was read)",
                                                  phrase_only=not meaningful(und.remainder), facts=prompt_facts)
                    phrase_only = not meaningful(und.remainder)
                    used_llm = True
                except LLMError:
                    candidate = None
        else:
            # המודל קורא קודם. תוכנית הקורא חושבה לפני כל שינוי במצב: גיבוי, ובדיקה צולבת כשנקרא במלואו.
            cross = _expected_values(snapshot, plan) if plan.complete else {}
            if not und.complaint:              # a complaint keeps the earlier offer: the reply repeats it
                state.unanswered_question = None   # otherwise the LLM offers the representative itself
            try:
                understood, candidate = self._llm_turn(ctx, history, text, plan, deadline, cross, conflicts, und,
                                                       facts=prompt_facts)
                used_llm, path = True, "llm"
            except LLMError:
                log.warning("llm turn failed; falling back to the parser")
                understood, path, llm_failed = False, "fallback", True
            if ctx.eligible_requested:
                # the catalog's list IS the answer: never also "במילים פשוטות: אילו רכבים לבדוק…" (live 15/11: a
                # follow-up after an answer about the cars was read as "explain the topic again")
                und = replace(und, eligible=ctx.eligible_drive, topic=ELIGIBLE_TOPIC, unanswered=[], clarify=False,
                              topic_lines=[])
                state.last_topic = ELIGIBLE_TOPIC
                state.eligible_turns = 0
                state.eligible_unclear_asked = False
                state.unanswered_question = None
                state.last_rep_question = None
                if not ctx.eligible_drive and not state.family_note_told and state.profile.children is None:
                    und = replace(und, family_note=True)
                    state.family_note_told = True
            answered = {_FAQ_OF_ARG.get(k, k) for k, v in profile_args(state.profile).items()
                        if v is not None and data_before[0].get(k) is None}
            if answered & set(und.faq_ids):
                # "קרן השתלמות? גם את זה אין לי": the field named with "?" was answered in the same message, so its
                # explanation is not owed (live Gemini 1/11: a guard replaced the model's reply with the FAQ answer)
                und = replace(und, faq_ids=[i for i in und.faq_ids if i not in answered])
            if ctx.cars_shown:
                # the model understood a request to see the cars and code shows them: answered, never offered to the
                # representative or "not understood" (live Gemini 1/11: "יש איזה רשימה לבחור ממנה?" got the offer)
                understood, und = True, replace(und, unanswered=[], clarify=False)
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

        # "את השכר אבדוק אחר כך": postpone a field, after this turn's values were saved (so a field just
        # supplied is never deferred). The acknowledgement is added to the reply below.
        defer_ack = _defer_change(state, snapshot, text, set(before_turn.changed(state)))
        # each missing field on its own (16/11): one asked and passed over twice is postponed, never asked over and over
        defer_ack = " ".join(a for a in (defer_ack, _pass_over(state, before_turn)) if a)
        # the missing data the question is about, after this turn's saves (data_facts, 16/11): the facts answer it, so it
        # is never also "no information, ask the representative"
        still = [f for f in concerned if f in data_facts.missing_now(state)]
        if ctx.copay_choice_needed:
            # "לא ידוע" to the copay choices: understood, answered with what to choose; the choices follow as they were
            understood, und = True, replace(und, resume=True)
        if still and (und.unanswered or und.clarify):
            und = replace(und, unanswered=[], clarify=False)     # answered: never also "לא בטוח שהבנתי"
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
        # "עדכנתי: קבוצה ג" is said as well: never "רשמתי: קבוצה ג." next to it (15/11)
        said = (notice or "").startswith(("רשמתי", "עדכנתי")) or plan.acked
        confirmations = before_turn.confirmations(state, snapshot, just_calculated, said)
        if plan.notice is not None and plan.notice.startswith((COPAY_WITH_REP[:40], MAINTENANCE_WITH_REP[:40])):
            # the value is pending with the rep, not "unknown, look at the payslip": that line says it instead
            confirmations = [line for line in confirmations
                             if line not in (CURRENT_UNKNOWN_ACK, MAINTENANCE_UNKNOWN_ACK)]

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
        answers, terms = self._answers(snapshot, und, state)
        # the verified explanations the model's own may replace (reply contract, 16/11): the FAQ answers and topic lines
        # asked for. Code facts (the open cars, the catalog, the results) are never replaced
        explainable = [e.answer for e in (snapshot.faq.get(i) for i in und.faq_ids) if e is not None] + [
            line for line, _ in und.topic_lines]
        if "ai_engine" in und.faq_ids and not used_llm:
            answers.append(ENGINE_OFFLINE)   # "אתה באמת Gemini?" answered by a template: it says so (31/10)
            terms.append("Gemini")
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
        faq = snapshot.faq
        social_line = ""
        if und.social:                                  # small talk: a few words first, then the open question
            social_line = _turn(faq.social_replies.get(und.social, ()), state.social_told)
            state.social_told += 1
        feedback_line = ""
        if und.complaint:                               # feedback on the last reply: acknowledged in turn
            feedback_line = _turn(faq.feedback_replies, state.feedback_told) or "צודקים, סליחה."
            state.feedback_told += 1
        lines = [*([COPAY_UNKNOWN_CHOOSE] if ctx.copay_choice_needed else []),
                 *([social_line] if social_line else []),
                 *extra, *([defer_ack] if defer_ack else []), *no_car_lines,
                 *([feedback_line] if feedback_line else []),
                 *([_audit_text(state)] if und.audit else []),
                 *([ALL_AT_ONCE] if und.all_at_once else []),
                 *([snapshot.faq.redirect] if und.redirect and not und.complaint else []),   # the rep offer: plan_reply
                 # a truly unclear question: one line, no buttons, never the representative
                 *([snapshot.faq.clarify] if und.clarify and und.action is None
                   and not (und.redirect or und.unanswered or und.complaint) else [])]
        # a turn that calculated or ranked answers with its results; no explanation is added to them (15/11: "ומה
        # שלושת הכי משתלמים?" opened with every car's copay)
        about_results = (und.about_results and state.stage == Stage.PRESENTED and bool(state.results)
                         and not just_calculated)
        if about_results and plan.notice is None:
            # "ולמה דווקא הראשון?" / "למה ה-MG4 בראש?": a question pointing at a car on screen — named, written in other
            # letters or with two prefix letters ("שהדולפין"), or by its place on the list — and not about the estimate
            # itself gets the engine's reason for that car (8870, 15/11: it got every car's copay). Any other question
            # about the results is answered as before: the estimate's status and its copay.
            focus = _results_focus(state, snapshot, und, text)
            if pair is not None:
                # "why" between two results (stage 2, 16/11): the engine's two nets and their difference are code's fact,
                # said as is; the components that make the difference are the explanation the model may word
                found = pair_facts(*pair)
                lines.append(found.net)
                lines.extend(found.reasons)
                explainable.extend(found.reasons)
            else:
                reason = results_reason_text(state, focus) if focus and not asks_estimate(snapshot, text) else ""
                # one result, or a question about the estimate itself: the engine's reason / the estimate's status
                lines.append(reason or results_explain_text(state, und.result_focus))
        # a question about missing data (data_facts, 16/11), with what is still missing after this turn's saves: the
        # facts are the verified answer; the model may word the explanation, the rule line is code's, said as is
        if still:
            missing_lines = data_facts.facts(state, snapshot, still)
            lines.extend(missing_lines)
            explainable.extend(missing_lines[:-1])
        # a request the bot cannot do yet ("תשווה ביניהם" before the profile is complete): what is needed
        if (und.compare and not just_calculated and not map_shown and not still
                and state.stage not in (Stage.PRESENTED, Stage.WAITING_FOR_REP) and RANK_DEFERRED not in confirmations
                and COMPARE_WAITS not in confirmations):   # the selection made this turn already says it waits
            need = compare_needs_text(state)
            # "what it depends on" answers a general question; cars named are a request, read as one (live QA 31/10)
            if need != CHOOSE_TO_COMPARE or not named_cars(text, snapshot.catalog):
                lines.append(need)
        # answered with what it depends on: the question that follows is the same one, picked up, not "still missing"
        compare_answered = CHOOSE_TO_COMPARE in lines
        # A turn that gave no data (small talk, a question, feedback, a style request, advice asked too early, or a
        # remark that is no answer at all) is answered first and then ONE light question follows: never the whole
        # compound question with an example, never "לא הצלחתי להבין את התשובה" for something that was no answer (30/10).
        attempted = answer_like(snapshot, und.remainder)
        changed = (dict(profile_args(state.profile)), list(state.selected)) != data_before \
            or _car_key(state) != car_before
        # not light: "all the questions at once" asks for everything; a bare greeting gets the opening question (a
        # number typed after it must still bind to the field it names); a remark that is no answer keeps the
        # not-understood flow (simpler words, an example), only its first line changes (templates.UNSURE)
        light = (not changed and not problems and (und.anything or und.resume)
                 and not und.all_at_once and und.social != "greeting")
        p = state.profile
        fresh = (all(v is None for v in data_before[0].values()) and p.group is None and p.occupation is None
                 and p.occupation_text is None and state.current.source is None
                 and not state.current.query and not state.current.has_car)     # nothing known yet
        open_help = light and und.social in ("how_are_you", "thanks") and fresh

        def build_plan():
            built = plan_reply(
                state, snapshot, understood=understood or bool(problems) or und.anything,
                just_calculated=just_calculated, matched_car=matched_car, notice="\n".join(problems) or notice,
                rep_opened=rep_opened, notice_asks=bool(problems) or plan.asks is not None, cta_field=asks_field,
                confirmations=[*confirmations, *refused], answers=list(zip(answers, terms)),
                # feedback on the last reply: the open question again in other, simpler words (never the same template)
                follow_up=und.presence, explain=und.help or und.complaint or len(no_car_lines) > 1, extra=lines,
                # after an explanation the waiting question comes back as it was, picked up (16/11), never turned into
                # "עדיין חסר לי…", which reads as if the employee had not answered
                about_results=about_results,
                resume=und.resume or compare_answered or (light and bool(answers) and state.asked_field in _ASKED_Q2),
                light=light, attempted=attempted,
                open_help=open_help)
            if map_line:
                built.ack.append(map_line)  # after what was saved and identified this turn
            # the open cars are counted from the state after this turn's saves: the change is said first (16/11)
            built.saved_first = und.eligible is not None
            if und.show_list:
                built.ask = []      # the list ends with its own question (1/11)
            if und.payslip_request:
                built.ask = []      # "אני רוצה להעלות תלוש": the upload first; the flow question waits (1/11)
            if und.correction is not None:
                built.ask = []      # the correction's own question is the reply's question (1/11)
            if map_shown and state.stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
                built.ask = []      # the map answered the request; the call to action still offers a comparison
            return built

        def decide(built: ReplyPlan) -> ReplyPlan:
            """The turn decision on the plan: what was understood, saved and is still needed (reply contract)."""
            built.intent = _intents(und, bool(before_turn.changed(state)))
            built.changes = {f: str(v) for f, v in before_turn.changed(state).items()}
            built.missing = calc_missing(state)
            built.results = just_calculated
            built.explainable = list(explainable)
            built.answer_source = "template" if explainable else None
            if still:
                built.intent.append("explain:missing_data")
            return built

        reply_plan = decide(build_plan())
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
        actions.mark_offer(state, snapshot)      # the bot's one offer of the upload (23/11)
        # what this reply is about, for the next message's follow-up (19/11): the action it offered (its link), and its
        # topic — the action's own when the turn was about it, else the term explained
        offered = [state.link_now] if state.link_now in actions.LABELS else []
        about_action = und.action is not None or (und.payslip_request and bool(offered))
        group_fields = current_group(state)[1]
        state.last_reply = ReplyContext(
            topic=actions.TOPICS.get(offered[0]) if about_action and offered else state.last_topic,
            asked_field=group_fields[0] if len(group_fields) == 1 else None, offered=offered)
        requests = dict(question_requests=ctx.question_requests, cancel_question_ids=ctx.cancel_question_ids,
                        copay_selection=ctx.copay_selection)
        draft = reply_plan.text()
        # results and the rep messages keep their exact template text; buttons get the template (fast)
        if (choice or reply_plan.fixed or rep_opened or ctx.eligible_requested or candidate is None or not self.llm.available
                or self.mode == "parser_first" or map_shown):     # the map's summary is code's, like results
            say = ("outage" if llm_failed else "not_tried" if candidate is None or not used_llm else "fixed")
            draft = self._with_cars(ctx, draft, from_model=False)
            state.asked_field = asked_field(state, draft)
            return TurnOutcome(reply=draft, source="template", just_calculated=just_calculated, path=path,
                               say=say, decision=reply_plan.decision(), **requests)
        # ---------- the reply contract (16/11): one turn decision, composed once ----------
        # Code owns the acknowledgements (from the state diff), the next question and the call to action; the model's
        # part is the explanation the turn owes, checked by the facts guards (sources, numbers, the claim verifier).
        # Its own acknowledgements, questions and calls to action are never taken (RCA 16/11: two authors reconciled
        # by text patches lost the selection, added unrelated questions and acknowledged values twice).
        attribute_q = bool(und.unanswered) and any(asks_attribute(snapshot, q) for q in und.unanswered)
        # code's rules could not read the message (no value, no known question): the model's understanding counts — a
        # remark or an intention it answered ("לא נורא, נמצא אותו יחד") is the turn's answer, never "לא בטוח שהבנתי"
        # a decision code reached only weakly (19/11: c204812 left these to templates, so "איפה הכפתור?" got "לא בטוח
        # שהבנתי" with the model available): an unclear question, or "explain again" with no referent found. The model
        # reads it with the history and the actions on screen; its answer passes the same guards, and code keeps the
        # question, the actions and every value
        # "explain again" (help) is not weak: code has its own answer (the question on screen in simpler words), and the
        # model's own call to action must never stand next to code's choices (the copay's three choices)
        weak = und.clarify
        code_lost = not (understood or problems or und.anything) or weak
        answer, blocked, say = "", False, "llm:composed"
        if explainable or (und.unanswered and not attribute_q) or code_lost:
            answer, blocked, say = self._model_answer(candidate, draft, ctx, history, text, deadline,
                                                      owed=bool(explainable or und.unanswered), facts=facts,
                                                      facts_numbers_only=pair is not None)
        if answer and explainable:
            reply_plan = reply_plan.with_explanation(answer)
        elif answer and und.unanswered:
            # a question no FAQ entry covers, answered from STATE / the catalog / the FAQ and verified: before code's ask
            reply_plan = replace(reply_plan, answers=[*reply_plan.answers, answer], answer_source="model")
        elif answer and code_lost:
            understood = True                      # read by the model: the decision is the understood one
            state.misses = 0
            if weak:
                # the model's answer replaces code's "say it again" / "in simpler words" (never both)
                und = replace(und, clarify=False, help=False)
                lines[:] = [x for x in lines if x != snapshot.faq.clarify]
            reply_plan = decide(build_plan())
            reply_plan = replace(reply_plan, answers=[answer, *reply_plan.answers], answer_source="model")
            state.last_ask = reply_plan.ask[-1] if reply_plan.ask else ""
        if (und.unanswered and not answer and state.unanswered_question is None and not rep_skipped
                and not und.redirect):
            # nothing verified answers a concrete question about the arrangements (or a car's property the catalog does
            # not hold): code offers the representative, with its buttons, exactly as without a model
            state.unanswered_question = und.unanswered[0]
            reply_plan = decide(build_plan())
            state.last_ask = reply_plan.ask[-1] if reply_plan.ask else ""
        source = "llm" if reply_plan.answer_source == "model" else "template"
        reply = reply_plan.text()
        reply = self._with_cars(ctx, reply, from_model=False)
        state.asked_field = asked_field(state, reply)
        return TurnOutcome(reply=reply, source=source, guard_blocked=blocked, just_calculated=just_calculated,
                           path=path, say=say, decision=reply_plan.decision(), **requests)

    @staticmethod
    def _answers(snapshot: RulesSnapshot, und: Understanding,
                 state: Optional[ConversationState] = None) -> tuple[list[str], list[str]]:
        entries = [e for e in (snapshot.faq.get(i) for i in und.faq_ids) if e is not None]
        # the cars open to the employee (15/11): counted by code from the state after this turn's saves
        eligible = ([_eligible_line(state, snapshot, und)] if und.eligible is not None and state is not None else [])
        return ([e.answer for e in entries] + [line for line, _ in und.topic_lines] + eligible + und.catalog,
                [e.term for e in entries] + [term for _, term in und.topic_lines] + ["רכבים"] * len(eligible)
                + ["קטלוג הרכבים"] * len(und.catalog))

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
                  und: Understanding, applied: str = "", phrase_only: bool = False,
                  facts: str = "") -> tuple[bool, Candidate]:
        """The LLM leads the turn: it saves through tools (each call checked in code, exactly as before),
        answers and asks; the reply it writes is guarded in _finish before it is sent."""
        state, snap = ctx.state, ctx.snapshot
        recorded = [False]

        def execute(call: ToolCall) -> dict[str, Any]:
            if call.name == "calculate":
                call = ToolCall("select_options", call.args, call.id)
            if call.name not in LLM_TOOLS:
                return {"ok": False, "error": "unknown tool"}
            if phrase_only:
                return {"ok": False, "error": "nothing to save: the system already read the whole message"}
            if call.name == "propose_rep_question" and state.rep_skipped:
                return {"ok": False, "error": "the employee chose to continue without the representative"}
            if call.name == "propose_rep_question" and ctx.eligible_requested:
                return {"ok": False, "error": "the system answered this eligibility question from the catalog"}
            if call.name == "answer_eligible_cars":
                if und.catalog:
                    return {"ok": False, "error": "this question asks about the full catalog, not eligibility"}
                # the drive is a filter on a catalog question, not the employee's data: the model reads it from the
                # meaning ("לא צריך לתדלק", "רק על סוללה", 15/11), the cars and the count come from the catalog, and
                # the reply names the drive it filtered by. Refused only against a drive the employee named otherwise
                requested = {str(d) for d in [*(call.args.get("drives") or []), call.args.get("drive")] if d}
                spoken_drive = match_drive(text, named_only=True)
                if requested and spoken_drive is not None and spoken_drive.value not in requested:
                    return {"ok": False, "error": f"the employee named {spoken_drive.value}, not {sorted(requested)}"}
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
            if checked.name not in ("propose_rep_question", "show_cars"):
                recorded[0] = recorded[0] or _recorded(facts)
            return facts

        messages = [system_message(state, snap, "" if applied or not plan.complete else parser_hint(plan.calls), applied,
                                   facts),
                    *history[-HISTORY:], user_message(text)]
        # phrase_only: code read the whole message (a request about the conversation, "שאלה אחר שאלה"): the model
        # writes the reply and changes nothing (live Gemini 1/11: it deferred the pension the employee never mentioned)
        tools = () if phrase_only else _understand_tools(state)
        cand = run_rounds(self.llm, messages, tools, execute, deadline, force_on_claim=not applied)
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
            ok = (zero_grounded(key, text, ext.zero_cues, ext.contribution_terms, ext.nothing_deducted_phrases,
                                asked=ctx.state.asked_field, bare=(ext.bare_zero_negatives, ext.bare_zero_words))
                  if _is_zero(value)
                  else grounded_number(value, numbers_text) or _given_earlier(ctx.state, key, value))
            if not ok:
                refused.append(key)
        if (kept.get("eligibility_group") and not group_named(text, snap.calc.group_order, FAMILY_GROUP)
                and not _given_earlier(ctx.state, "eligibility_group", kept["eligibility_group"])):
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
        # a car the model picked and the employee wrote in other letters ("מג 4", "לנירו", 15/11): named as well
        named |= {o.id for o in resolved if o and o.car is not None
                  and any(sounds_like(text, n) for n in catalog_names_of(o.car))}
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
            # A sweeping word with no field named ("הכל 5", "אין לי כלום") is never spread, whatever is on screen: live
            # Gemini (15/11) saved "פנסיה 5%" from "הכל 5" after its own reply had asked one field, so the question on
            # screen was not "grouped" and the spread across four fields passed; or as separate calls, one field each
            sweeping = has_all_scope(text) and not _q2_fields_named(text)
            if (grouped and not _q2_fields_named(text)) or sweeping:
                spread = [k for k in args if k in _ASKED_Q2 and args.get(k) is not None]
                if (len(spread) > 1 and (has_all_scope(text) or bare_value(text))) or (spread and sweeping):
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
            # a bare value ("34") or a bare "no" ("גם לא") answers only the field on screen: any other field is
            # refused, never remapped
            bound = state.asked_field
            ext = ctx.snapshot.extensions
            if bound and (bare_value(text) or bare_zero_answer(text, ext.bare_zero_negatives, ext.bare_zero_words)):
                wrong = [k for k, v in args.items() if k in _NUMERIC_ARGS and v is not None and k != bound]
                if wrong:
                    log.info("bare value for %s refused (asked %s)", ",".join(wrong), bound)
                    return None
            elif (bare_value(text) and current_group(state)[0] == "Q1" and not asks_base(state)
                  and args.get("base_salary") is not None):
                # the greeting asks the group, not the pay: "16,400" alone is not guessed to be the base (22/10)
                log.info("bare value for base_salary refused (the group or the role is asked)")
                return None
            # a value said as an estimate ("בערך 2.5%") is saved as one, whoever read it: marked in the results and
            # replaced by the exact value when it comes (16/11: the model's save of "בערך 2.5%" was recorded as exact)
            approx = [k for k in _NUMERIC_ARGS if args.get(k) is not None and not _is_zero(args[k])
                      and said_approximately(text, args[k])]
            if approx:
                args = {**args, "approximate_fields": sorted({*(args.get("approximate_fields") or []), *approx})}
            return ToolCall(call.name, args, call.id)
        if call.name == "rank_options" and not und.rank and und.map_all:
            # ranking replaces the cars to compare: never on "השווה את כל האפשרויות" (the map keeps the choice) — live
            # transcript 22/10. Any other wording of the request is the model's reading ("אלה שהכי פחות יורידו לי
            # מהמשכורת", "שלושת הכי שווים", 15/11: refused for want of a ranking word); the ranking is the engine's
            log.info("rank_options on a request for the map; refused")
            return None
        if (call.name in ("rank_options", "select_options", "calculate") and und.about_results and not und.rank
                and ctx.state.results):
            # a question about the results on screen ("למה דווקא הראשון?") is answered from them, by code; it never
            # ranks or selects again (stage 1 of the reply contract, live 16/11: the ranking was shown again)
            log.info("%s on a question about the results on screen; refused", call.name)
            return None
        if call.name == "rank_options" and not und.rank and named_cars(text, ctx.snapshot.catalog):
            # cars named without a ranking word ("אני רוצה להשוות את MG4 ונירו") are a comparison of those cars, never a
            # ranking of all of them (live 16/11: the model ranked next to the selection it had just made)
            log.info("rank_options next to named cars without a ranking word; refused")
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
            # the model's guess, refused: never turned into code's question "no car, or copay 0?" (live QA 1/11: "אני
            # לא זוכרת, תציג לי אפשרויות" got that question). Only the employee's own words raise it (und.ambiguity).
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

    def _request_new_car_copay(self, ctx: ToolContext, option: Any, policy: str, replace: bool = False,
                               employee_note: Optional[str] = None) -> None:
        """The representative is asked the copay of a new car compared (C1): the catalog amount to confirm, or — when
        the catalog has none (C2) — the amount itself; a copay the employee expects goes with it. Context: the group,
        the car, the catalog amount and that expectation only — never pay data.

        employee_note: the employee's own question (rep-question endpoint, copay verification). Appended to
        question_text so the representative reads it verbatim; the answer still updates this car's copay by
        option_id, never by the text."""
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
        if employee_note:
            text += f"{EMPLOYEE_QUESTION_MARK}{employee_note}"
        self._request(ctx, MissingField.NEW_CAR_COPAY, mode, AnswerType.MONEY, text, context,
                      str(default) if default is not None else None, option.id, replace=replace)

    def rep_question(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                     question: str, missing_field: MissingField, option_id: Optional[str],
                     existing_text: Optional[str], record_run: RecordRun) -> TurnOutcome:
        """Employee-initiated question to the representative (rep-question endpoint, Package A). Runs OUTSIDE the
        conversation turn: no understand, no parser, no LLM. The service has already decided the type and validated
        the car against the state; this only builds the question and marks the result.

        missing_field is NEW_CAR_COPAY only when the service confirmed a structured copay verification with a valid
        car in the authorised results; otherwise GENERAL_QUESTION (the safe informational default). existing_text is
        the question_text of an OPEN question already held for this key (de-dup, no replace): when set we never
        overwrite it — "exists" is returned with the saved text so a differently-worded new question is never lost
        silently and the employee is told the earlier one is still open. GENERAL_QUESTION never touches the
        calculation; NEW_CAR_COPAY leaves the card on the catalog amount (copay_pending) until the rep answers."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        if missing_field == MissingField.NEW_CAR_COPAY:
            option = next((o for o in available_options(snapshot, state)
                           if o.id == option_id and o.car is not None), None)
            on_screen = any(r.option_id == option_id
                            and r.result.get("copay_source") in ("catalog", "catalog_final")
                            for r in state.results)
            if option is None or not on_screen or option.car.default_copay is None:
                # the car is no longer a catalog-copay option on screen: fall back to the safe informational path
                missing_field, option_id = MissingField.GENERAL_QUESTION, None
        if missing_field == MissingField.NEW_CAR_COPAY:
            catalog_amount = str(option.car.default_copay)
            # the car is the option_id, never the text: a question that names another catalog car (a card opened
            # for NIRO still holding a question about DOLPHIN) is refused, so the answer can never land on a car
            # the representative was not asked about
            others = [c for c in cars_named(snapshot, question)
                      if c.catalog_code != option.car.catalog_code and c.model_name not in option.car.model_name]
            if others:
                named = ", ".join(c.label for c in others)
                return TurnOutcome(reply="", source="rep", path="choice",
                                   rep_question_result=RepQuestionResult(
                                       status="invalid", missing_field=missing_field.value, option_id=option_id,
                                       car_label=option.label, catalog_amount=catalog_amount,
                                       message=f"השאלה מזכירה את {named}, אבל הפנייה היא על {option.label}. "
                                               f"כתבו את השאלה על {option.label}, או פתחו פנייה מהכרטיס של "
                                               f"{named}."))
            if existing_text is not None:
                return TurnOutcome(reply="", source="rep", path="choice",
                                   rep_question_result=RepQuestionResult(
                                       status="exists", missing_field=missing_field.value, option_id=option_id,
                                       car_label=option.label, catalog_amount=catalog_amount,
                                       question_text=employee_wording(missing_field, existing_text)))
            self._request_new_car_copay(ctx, option, snapshot.extensions.copay_policy, employee_note=question)
            state.unanswered_question = state.last_rep_question = None   # asked: not offered again
            if option_id not in state.rep_copay_asked:
                state.rep_copay_asked.append(option_id)
            for item in state.results:
                if item.option_id == option_id:
                    item.result.update(copay_source="catalog", copay_pending=True)
            return TurnOutcome(reply="", source="rep", path="choice", question_requests=ctx.question_requests,
                               rep_question_result=RepQuestionResult(
                                   status="created", missing_field=missing_field.value, option_id=option_id,
                                   car_label=option.label, catalog_amount=catalog_amount, question_text=question))
        if missing_field == MissingField.CURRENT_COPAY:
            # today's copay (1/11): the representative answers with an amount; it is offered to the employee and
            # applied only on their confirmation (apply_answer), never by itself
            car = rep_car_label(state.current, snapshot)
            if existing_text is not None:
                return TurnOutcome(reply="", source="rep", path="choice",
                                   rep_question_result=RepQuestionResult(
                                       status="exists", missing_field=missing_field.value, car_label=car,
                                       question_text=existing_text))
            hint = catalog_copay_hint(state, snapshot)
            qid = today_copay_question_id(state)
            self._request(ctx, MissingField.CURRENT_COPAY, Mode.CONFIRM, AnswerType.MONEY, question,
                          {"group": state.profile.group, "car_label": car}, suggested=str(hint) if hint else None,
                          option_id=qid)
            state.unanswered_question = state.last_rep_question = None
            return TurnOutcome(reply="", source="rep", path="choice", question_requests=ctx.question_requests,
                               rep_question_result=RepQuestionResult(
                                   status="created", missing_field=missing_field.value, option_id=qid, car_label=car,
                                   catalog_amount=str(hint) if hint else None, question_text=question))
        if missing_field == MissingField.MAINTENANCE_AMOUNT:
            # no car today (19/11): the allowance the employee receives, asked from the representative as today's copay
            # is. The rep sees the group and "no company car" only, never pay; the answer completes the value
            qid = today_maintenance_question_id(state)
            if existing_text is not None:
                return TurnOutcome(reply="", source="rep", path="choice",
                                   rep_question_result=RepQuestionResult(
                                       status="exists", missing_field=missing_field.value, option_id=qid,
                                       car_label=NO_CAR_LABEL, question_text=existing_text))
            hint = state.payslip_maintenance
            self._request(ctx, MissingField.MAINTENANCE_AMOUNT, Mode.CONFIRM, AnswerType.MONEY, question,
                          {"group": state.profile.group, "car_label": NO_CAR_LABEL},
                          suggested=str(hint) if hint is not None else None, option_id=qid)
            state.unanswered_question = state.last_rep_question = None
            return TurnOutcome(reply="", source="rep", path="choice", question_requests=ctx.question_requests,
                               rep_question_result=RepQuestionResult(
                                   status="created", missing_field=missing_field.value, option_id=qid,
                                   car_label=NO_CAR_LABEL, catalog_amount=str(hint) if hint is not None else None,
                                   question_text=question))
        # GENERAL_QUESTION: informational, never changes the calculation. option_id is the content hash, so the
        # same question is recognised (never sent twice) while different questions open their own case.
        if existing_text is not None:
            return TurnOutcome(reply="", source="rep", path="choice",
                               rep_question_result=RepQuestionResult(
                                   status="exists", missing_field=MissingField.GENERAL_QUESTION.value,
                                   question_text=existing_text))
        gq_id = general_question_id(question)
        # the car the question is about: the catalog cars it names; only a question that names none is about the
        # car the employee drives today
        named = cars_named(snapshot, question)[:MAX_OPTIONS]
        car = (", ".join(c.label for c in named) if named
               else rep_car_label(state.current, snapshot) if state.current.source in ("catalog", "tax", "stated")
               else None)
        self._request(ctx, MissingField.GENERAL_QUESTION, Mode.CONFIRM, AnswerType.TEXT, question,
                      {"group": state.profile.group, "car_label": car}, option_id=gq_id)
        state.general_questions[gq_id] = GeneralQuestion(text=question)
        state.unanswered_question = state.last_rep_question = None       # asked: not offered again
        return TurnOutcome(reply="", source="rep", path="choice", question_requests=ctx.question_requests,
                           rep_question_result=RepQuestionResult(
                               status="created", missing_field=MissingField.GENERAL_QUESTION.value,
                               option_id=gq_id, car_label=car, question_text=question))

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
        if (state.pending_rep is not None or open_profile(state.profile) or state.current.source is None
                or missing_current_car(state.current)):
            # first the profile, the car itself and its own values (the copay): what the employee knows is asked
            # before the wait (15/11: the wait began right after the car, and "300" typed then was dropped)
            return
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
        elif field == MissingField.CURRENT_COPAY and event.option_id.startswith(TODAY_COPAY_PREFIX):
            # the employee's own inquiry about today's copay (1/11): the amount is offered, never applied by itself
            c = state.current
            state.rep_copay_offer = RepCopayOffer(question_id=qid, amount=amount, car_label=c.label or "")
            lines.append(rep_copay_offer_text(c.label or "", amount, c.copay, c.copay_provisional))
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
        elif field == MissingField.MAINTENANCE_AMOUNT and (event.option_id or "") == TODAY_MAINTENANCE_ID:
            # the employee's own inquiry about their allowance (no car today, 19/11): the answer completes the value
            # and the calculation runs again — unless the employee gave a value of their own meanwhile, which stays
            c = state.current
            if c.source == "none" and c.maintenance is None:
                c.maintenance, c.maintenance_unknown = amount, False
                state.field_sources["current_maintenance"] = "representative"
                if "current_maintenance" in state.profile.approximate_fields:
                    state.profile.approximate_fields.remove("current_maintenance")
                invalidate_results(state)
                lines.append(rep_today_maintenance_text(amount, None))
            else:
                lines.append(rep_today_maintenance_text(amount, c.maintenance if c.source == "none" else None))
            if "maintenance" in state.rep_fields:
                state.rep_fields.remove("maintenance")
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
        follow: list[str] = []                 # the advisor's next step, after the answer and what it changed
        map_had = state.option_map is not None
        if self._resolve_map(ctx):
            # the representative's answer completed the map (presented now, as it was asked for) or changed it
            follow.append(map_reply(state) if not map_had else MAP_UPDATED)
        state.stage = next_stage(state)
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        if field == MissingField.NEW_CAR_COPAY:
            if show_results and state.results:
                follow.append(results_text(state))
        elif field == MissingField.GENERAL_QUESTION and state.stage == Stage.PRESENTED:
            pass                                   # the results stay on screen; nothing else to ask
        elif not (field == MissingField.OCCUPATION_OPT_OUT and state.profile.rep_opt_out_allowed):
            follow.append(render(state, snapshot, understood=True, just_calculated=just_calculated,
                                 rep_opened=rep_opened))
        reply, follow_up = "\n\n".join(x for x in lines if x), "\n\n".join(x for x in follow if x)
        if not reply:                          # nothing from the representative to show: one message, as before
            reply, follow_up = follow_up, ""
        return TurnOutcome(reply=reply, source="rep", just_calculated=just_calculated, path="rep",
                           question_requests=ctx.question_requests, copay_selection=ctx.copay_selection,
                           follow_up=follow_up)

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
            if pending_text not in (item.result.get("assumptions") or [""]) and not any(
                    "אושר על ידי נציג התחבורה" in a for a in item.result["assumptions"]):
                # a catalog amount that was final (CATALOG_DEFAULT) and that the employee asked to confirm
                item.result["assumptions"].append(confirmed_assumption(item.label, event.effective_date))
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
        state.results_version += 1
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

    def _model_answer(self, cand: Optional[Candidate], draft: str, ctx: ToolContext, history: Sequence[ChatMessage],
                      text: str, deadline: Optional[float], owed: bool = True,
                      facts: str = "", facts_numbers_only: bool = False) -> tuple[str, bool, str]:
        """The model's part of the reply contract (16/11): the explanation the turn owes, with the facts guards —
        valid sources, numbers only from STATE / the tools / the FAQ / the employee, the claim verifier, style. One
        retry with the violations as feedback, then the answer without the offending sentences if it still stands,
        else "" (the turn decision's own verified explanation is used). Returns (answer, number_blocked, say)."""
        if cand is None or not cand.text:
            return "", False, "empty"
        state, snap = ctx.state, ctx.snapshot
        if (state.pending_ambiguity is not None or state.pending_clarification is not None
                or state.pending_confirmation is not None or state.pending_occupation is not None
                or ctx.range_problems):
            # code holds a question of its own (a held value, a role, an ambiguity): its exact words, nothing else
            return "", False, "pending"
        # a comparison is explained from its facts only (stage 2, 16/11): no other figure, even one STATE holds (the
        # model's "בערך 300 ₪ לטובת NIRO" took today's copay for a difference); else every figure the turn may say
        allowed = (collect_numbers([facts, text]) if facts_numbers_only
                   else self._allowed_numbers(ctx, history, text, cand, draft))

        # the actions the screen has on this reply: the persistent ones and this reply's link (19/11)
        on_screen = {k: LINK_LABELS[k] for k in [*actions.available(state, snap), *([state.link_now] if state.link_now
                                                                                     else [])] if k in LINK_LABELS}

        def judge(raw: str) -> tuple[str, Check, list[Violation]]:
            part = answer_part(raw)
            result = check_answer(part, snapshot=snap, allowed=allowed, tools_called=cand.tools_called)
            unsupported = self._claims(part, state, snap, deadline, facts) if part else []
            ui = ui_violations(part, on_screen, LINK_LABELS) if part else []     # never a button the screen lacks
            return part, result, [*result.violations, *ui, *(Violation("claim", s) for s in unsupported)]

        part, first, violations = judge(cand.text)
        if not violations:
            return first.text, False, "llm"
        if not owed and all(v.kind == "empty" for v in violations):
            return "", False, "llm:composed"     # nothing to add to a turn that owes no answer: no retry
        left = None if deadline is None else deadline - self.llm.clock()
        retry = self._retry(cand, violations, deadline) if left is None or left >= self.MIN_RETRY_S else ""
        if retry:
            part2, second, again = judge(retry)
            if not again:
                return second.text, False, "llm:retry"
            part, violations = part2, again
        if part and all(v.sentence for v in violations):
            trimmed = drop(part, [v.sentence for v in violations])      # on the tagged text
            result = check_answer(trimmed, snapshot=snap, allowed=allowed,
                                  tools_called=cand.tools_called) if trimmed else None
            if result is not None and result.ok:
                return result.text, False, "llm:trimmed"
        kinds = ",".join(sorted({v.kind for v in violations}))
        log.warning("llm answer rejected (%s); the verified explanation is used", kinds)
        return "", any(v.kind == "number" for v in violations), f"guard:{kinds}"

    def _claims(self, raw: str, state: ConversationState, snap: RulesSnapshot,
                deadline: Optional[float], facts: str = "") -> list[str]:
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
        # the comparison facts of this turn (stage 2) are a verified source too: the engine's own results
        knowledge = "\n\n".join(x for x in (render_faq(snap), render_catalog(snap), facts) if x)
        unsupported = verify(self.llm, knowledge, state_block(state, snap), items,
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

    def _rep_requested(self, ctx: ToolContext, und: Understanding) -> TurnOutcome:
        """The employee asks for the representative: the question of this message, else the latest one nothing
        answered, is offered (the inquiry card opens pre-filled with it); with none, the employee is asked what to
        ask. An earlier "להמשיך בלי" does not stop an explicit request. Already sent: its status, never twice."""
        state = ctx.state
        question = und.unanswered[0] if und.unanswered else state.last_rep_question
        known = known_question_line(state, question) if question else None
        if known is not None:
            return TurnOutcome(reply=known, source="template", path="rep_request")
        state.unanswered_question, state.rep_skipped = question or "", False
        state.asked_field = None
        return TurnOutcome(reply=rep_request_text(question), source="template", path="rep_request")

    def _prepare_rep_question(self, ctx: ToolContext) -> TurnOutcome:
        """"לשאול את נציג התחבורה": the question is rewritten without amounts or personal numbers, checked
        in code, and shown to the employee before anything is sent."""
        state, snap = ctx.state, ctx.snapshot
        question, state.unanswered_question = state.unanswered_question, None
        if not question:
            # a request for the representative with no question yet: what to ask (the next message is read as usual)
            plan = plan_reply(state, snap, understood=True, just_calculated=False,
                              confirmations=["מה תרצו לשאול את נציג התחבורה? כתבו את השאלה כאן."])
            return TurnOutcome(reply=plan.text(), source="template", path="choice")
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
            state.last_rep_question = None
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
