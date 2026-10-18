"""תור אחד בשיחה: UNDERSTAND → DECIDE → SAY.

UNDERSTAND: the parser, then one LLM call (data tools + note_message), checked in code.
DECIDE: code saves the values, advances the state machine, calculates, and builds a reply plan
(answers from faq.yaml, acks, one question, one call to action). SAY: the LLM writes the plan; the
template of the same plan when the LLM is off, fails, or breaks the number guard or the coverage check.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from app.modules.calculation.public import CopayStatus, ScenarioResult, evaluate
from app.modules.calculation.public import Status as CalcStatus
from app.modules.cases.public import AnswerType, MissingField, Mode, QuestionAnswered, QuestionRequest
from app.modules.rules.public import CatalogCar, RulesSnapshot, find_catalog_car, words
from app.modules.rules.public import catalog_names as catalog_names_of


from .expects import build_expects, choice_calls
from .fallback import (ZERO_ALLOWED, fields_plan, ParserPlan, car_roles, correction_plan, fallback_plan, named_cars, none_plan,
                       recorded_text)
from .guard import collect_numbers, violations
from .llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from .parsing import YEAR_RE, match_occupation, no_car_span, parse_copay, read_profile, redact_id_numbers
from .understanding import NO_CAR_OR_ZERO, NOTE_PROMPT, NOTE_TOOL, Understanding, merge_note, note_spec, understand
from .stage import (
    allowed_tools,
    current_group,
    MAX_OPTIONS,
    CarClarification,
    ConfirmedCopay,
    ConversationState,
    OptionResult,
    PendingRep,
    RepReason,
    Stage,
    blocking_rep_reason,
    missing_profile,
    open_profile,
    next_stage,
    Q2_FIELDS,
)
from .templates import (
    FIRST_LINE,
    UNANSWERED,
    unknown_ack_text,
    ReplyPlan,
    call_to_action,
    money,
    plan_reply,
    GREETING,
    OPT_OUT_DENIED,
    RANK_DEFERRED,
    REP_DEFERRED,
    chosen_options_text,
    confirm_group_text,
    approximate_assumptions,
    clarify_car_text,
    confirmed_assumption,
    group_question,
    conflict_text,
    queued_option_text,
    range_problem_text,
    render,
    rep_changed_text,
    rep_amount_assumption,
    rep_confirmed_text,
    rep_current_text,
    rep_maintenance_text,
    rep_opt_out_text,
    results_text,
    unusual_fix_text,
    why_sentence,
)
from .tools import (
    TOOL_SPECS,
    ToolContext,
    VehicleGetter,
    VehicleLookup,
    available_options,
    engine_inputs,
    option_state,
    resolve_option,
    run_tool,
)

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
# בשלבי האיסוף הסבב הראשון מחייב קריאה לכלי: החילוץ לא תלוי בכך שהמודל יבחר לקרוא לו
FORCED_TOOL_STAGES = (Stage.COLLECT_PROFILE, Stage.COLLECT_CURRENT_CAR)
HISTORY_MESSAGES = 8

EXTRACT_PROMPT = """You are the data-extraction step of a Hebrew-speaking company-car advisor at Strauss.
The employee writes in Hebrew. Your only job is to record what they said by calling the tools.
You never calculate, never answer in text, and never invent values.
Stage: {stage}. Still missing: {missing}.
{context}
Rules:
- Extract EVERY field the employee mentioned in this message in ONE save_profile call. A single message
  often holds the whole profile, e.g. "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%,
  קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים" -> occupation "מנהלת מוצר", base_salary 18500,
  commissions 0, bonus 0, pension_percent 6, study_fund_percent 2.5, credit_points 2.75, children_under_23 2.
- "אין" / "לא" / "בלי" for commissions (עמלות), bonus (בונוס) or study fund (קרן השתלמות) means 0.
- Percentages as plain numbers (6 for 6%). Money in shekels without separators (18,500 -> 18500).
- Never fill a field the employee did not mention: leave it out, the system will ask for it.
- If the employee does not know their current copay or use value, set the matching *_unknown flag.
- Car models in Latin letters as on the tax-authority list (e.g. "קורולה קרוס" -> "COROLLA CROSS").
- occupation: only a job title the employee names (e.g. "מנהלת מוצר"). Never a work pattern, place or
  description ("במשמרות", "במפעל", "מהבית") — leave occupation out.
- Never guess. Never expand a shortened number: for a salary, "18" or "בערך 18" is NOT 18,000. Pass the number
  exactly as written; the system checks it and asks the employee to confirm.
- If the employee gives a value as an estimate (כ-, בערך, בסביבות, בממוצע, משתנות), pass it and list that
  field's name in approximate_fields."""

SAY_PROMPT = """You write the next chat message of a Hebrew company-car advisor at Strauss, FROM A PLAN ONLY.
The plan is JSON with, in this order: answers (to the employee's questions), ack (what was saved),
follow_up, ask (the one question to ask now) and cta (the call to action).
- Cover every plan item, in that order, each on its own line or paragraph: answer the questions first.
  Never reply with the cta alone. Add no facts, advice or estimates.
- Use only the numbers that appear in the plan, written exactly as in the plan. Never calculate.
- Warm, natural and concise Hebrew: short sentences, one idea per line, at most 2-3 lines per paragraph.
  No chains of "·", no formulas, no parenthetical comparisons.
- Gender-neutral: plural forms (כתבו, בחרו) or neutral phrasing; never assume the employee's gender.
- The LAST line must be the cta, copied exactly, character for character.
Return only the message text."""

SAY_RESERVE_S = 0.3        # SAY needs at least this much of the turn budget; otherwise the template

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
    """תשובה לשאלה "הרכב שלך או חלופה?": mine / alternative / None (משהו אחר)."""
    ws = words(text)
    if not ws or len(ws) > 4:
        return None
    if ws in (["כן"], ["נכון"]) or ("הרכב" in ws and "שלי" in ws and "לא" not in ws):
        return "mine"
    if ws == ["לא"] or "חלופה" in ws:
        return "alternative"
    return None


def _dump(result: Any) -> dict[str, Any]:
    """The stored result: the engine's fields plus `why`, one plain sentence about the main cause
    (built from the drivers, so the client never builds text from numbers)."""
    out = result.model_dump(mode="json")
    out["why"] = why_sentence(out.get("drivers") or [])
    return out


def _with_assumptions(result: Any, state: ConversationState, option_id: str, label: str) -> Any:
    """הנחות שהמנוע לא יודע עליהן: ערכים משוערים, והשתתפות שהנציג אישר או קבע."""
    extra = approximate_assumptions(state)
    confirmed = state.confirmed_copays.get(option_id)
    if confirmed is not None:
        extra.append(rep_amount_assumption(label, confirmed.value, confirmed.effective_date) if confirmed.changed
                     else confirmed_assumption(label, confirmed.effective_date))
    return result.model_copy(update={"assumptions": result.assumptions + extra}) if extra else result


_YES = {"כן", "נכון", "בדיוק", "אכן", "yes", "לאשר", "אשר"}
_NO = {"לא", "no", "לתקן", "תקן"}


def _confirmation_plan(state: ConversationState, text: str) -> Optional[ParserPlan]:
    """תשובה ל"התכוונת ל-18,000 ₪?": כן → נשמר; לא → שואלים שוב את השדה; אחר → None."""
    ws = words(text)
    pending = state.pending_confirmation
    if not ws or len(ws) > 3 or pending is None:
        return None
    if ws[0] in _YES:
        state.pending_confirmation = None
        if pending.unusual and pending.field not in state.confirmed_unusual:
            state.confirmed_unusual.append(pending.field)
        args = {pending.field: str(pending.value),
                "approximate_fields": [pending.field] if pending.approximate else []}
        return ParserPlan([ToolCall("save_profile", args, "confirmed")], True, direct=True)
    if ws[0] in _NO:
        state.pending_confirmation = None
        notice = unusual_fix_text(pending.field) if pending.unusual else None
        return ParserPlan(complete=True, notice=notice, direct=True)
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
    if call.name == "calculate" and "options" in expected:
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
    if call.name not in ("match_current_car", "calculate"):
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
            # (it is probably an alternative) — ask, never confirm it with "אם זה לא הרכב שלך"
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
    """The data tools UNDERSTAND may run: everything said is saved whatever the stage (the stage is
    derived from the data), except while calculating or waiting for the representative."""
    if next_stage(state) in (Stage.CALCULATING, Stage.WAITING_FOR_REP):
        return ()
    return ("save_profile", "match_current_car", "list_options", "rank_options", "calculate")


def _is_zero(value: Any) -> bool:
    try:
        return Decimal(str(value)) == 0
    except Exception:  # noqa: BLE001 — not a number
        return False


_SEND = {"לשלוח", "שלח", "שלחו", "תשלח", "כן", "send"}
_CANCEL = {"לבטל", "בטל", "ביטול", "לא", "cancel"}


def _send_answer(text: str) -> Optional[bool]:
    ws = words(text)
    if not ws or len(ws) > 3:
        return None
    return True if ws[0] in _SEND else False if ws[0] in _CANCEL else None


def _ambiguity_answer(text: str) -> Optional[str]:
    """Typed answer to "אין לך רכב, או שיש רכב וההשתתפות 0?"."""
    if no_car_span(text):
        return "no_car"
    ws = words(text)
    if ws[:2] == ["יש", "רכב"] or ws[:1] == ["0"] or ("השתתפות" in ws and "0" in ws):
        return "zero_copay"
    return None


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
_HIDDEN_ARGS = {"commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
                "study_fund_percent": "study_fund_percent", "credit_points": "credit_points",
                "children": "children_under_23"}


_ACK_FIELDS = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")


def _values(state: ConversationState) -> dict[str, Any]:
    p, c = state.profile, state.current
    out = {f: getattr(p, f) for f in _ACK_FIELDS}
    # a catalog / tax-file use value is looked up, not said: acknowledged only when the employee states it
    out.update(copay=c.copay, use_value=c.use_value if c.source == "stated" else None, maintenance=c.maintenance)
    return out


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

    @classmethod
    def of(cls, state: ConversationState) -> "_Before":
        p = state.profile
        return cls((p.occupation, p.group), tuple(state.selected), state.rank_requested, blocking_rep_reason(state),
                   _values(state), current_group(state)[0], tuple(p.unknown_fields))

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
        if p.occupation and p.group and (p.occupation, p.group) != self.occupation:
            lines.append(confirm_group_text(p.occupation, p.group))
        if state.selected and tuple(state.selected) != self.selected and not just_calculated:
            labels = {o.id: o.label for o in available_options(snapshot, state)}
            lines.append(chosen_options_text([labels.get(i, i) for i in state.selected]))
        if state.rank_requested and not self.rank_requested:
            lines.append(RANK_DEFERRED)
        if state.pending_rep is None and self.rep_reason is None and blocking_rep_reason(state) is not None:
            lines.append(REP_DEFERRED)
        return lines


class Agent:
    def __init__(self, llm: LLMChain, mode: str = "llm_first"):
        self.llm = llm
        self.mode = mode          # llm_first | parser_first

    def greeting(self, state: ConversationState, snapshot: RulesSnapshot) -> str:
        _mark_asked(state)
        state.last_asked = list(current_group(state)[1])
        return "\n\n".join([GREETING.strip(), group_question(state, snapshot) + "\n" + FIRST_LINE,
                            call_to_action(state, snapshot)[0]])

    def run_choice(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, get_vehicle: VehicleGetter,
                   state: ConversationState, ids: list[str], label: str, record_run: RecordRun,
                   field: Optional[str] = None) -> TurnOutcome:
        """בחירה מובנית (כפתור) שכבר נבדקה מול expects: הקוד מיישם אותה ישירות — בלי הקורא ובלי מודל
        השפה. הערכים עוברים את אותם כלים ואותן בדיקות (טווחים, זכאות) כמו כל מקור אחר."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state, get_vehicle=get_vehicle)
        before = next_stage(state)
        field_ = field or build_expects(state, snapshot)["field"]
        if field_ == "ask_rep":
            return self._prepare_rep_question(ctx)
        if field_ == "rep_question":
            return self._rep_question_decision(ctx, ids[0] == "send", record_run)
        if field_ == "ambiguity":
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
            plan = _confirmation_plan(state, "כן" if ids[0] == "yes" else "לא") or ParserPlan(complete=True)
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
                   state: ConversationState, values: dict[str, Any], label: str, record_run: RecordRun) -> TurnOutcome:
        """הטופס המובנה (שדות הפרופיל, כבר נבדקו): נשמרים ישירות בכלי save_profile — בלי הקורא ובלי מודל
        השפה. אחרי תיקון, השלב נגזר מהנתונים: תוצאה קיימת מחושבת מחדש."""
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state, get_vehicle=get_vehicle)
        before = next_stage(state)
        state.pending_confirmation = None
        # הטופס הוא פעולה מפורשת של העובד (גם תיקון אחרי תוצאה), לכן לא עובר דרך _run ומגבלת הכלים של
        # השלב; run_tool נשאר השער היחיד לשמירה. השלב נגזר מהנתונים: calculated_for מתאפס ← חישוב מחדש.
        run_tool(ctx, "save_profile", values)
        held = state.pending_confirmation.field if state.pending_confirmation is not None else None
        saved = {k: v for k, v in values.items() if k != held}      # a rare value waits for "לאשר"
        plan = fields_plan(saved) if saved else ParserPlan([], True, direct=True)
        return self._finish(ctx, (), label, plan, before, record_run, choice=True)

    def run_turn(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        before = next_stage(state)

        und = understand(state, snapshot, text)
        if before == Stage.WAITING_FOR_REP:
            correction, reply = self._waiting(state, snapshot, text)
            if correction is None:
                # ממתינים לנציג: תשובה קצרה (או שאלת הבהרה), בלי שינוי בשלב, ברכב או בשאלות.
                # A question asked while waiting is still answered first.
                state.unanswered_question = und.unanswered[0] if und.unanswered else None
                extra = [UNANSWERED] if und.unanswered else [snapshot.faq.redirect] if und.redirect else []
                reply = "\n\n".join([*self._answers(snapshot, und)[0], *extra, reply])
                return TurnOutcome(reply=reply, source="template", path="waiting")
            before = self._start_correction(ctx)
            plan = correction
        elif state.pending_rep_question is not None and (send := _send_answer(text)) is not None:
            return self._rep_question_decision(ctx, send, record_run)
        elif state.pending_ambiguity is not None and (choice := _ambiguity_answer(text)) is not None:
            plan = _ambiguity_plan(state, choice)
        elif und.question_only and und.anything and und.ambiguity is None:
            # only questions / "מה?" / off topic: nothing to save, and a pending question stays open.
            # Without an FAQ match the LLM (when there is one) may still map the question.
            plan = ParserPlan(complete=not (und.unanswered and self.llm.available), direct=True)
        elif state.pending_confirmation is not None and (confirmed := _confirmation_plan(state, text)) is not None:
            plan = confirmed                     # "כן" / "לא" על "התכוונת ל-18,000 ₪?"
        elif state.pending_occupation is not None and (answer := _occupation_answer(state, text)) is not None:
            plan = _occupation_plan(ctx, answer)  # "כן" / מספר הצעה / "לא" על "לא מצאתי את ... בטבלה"
        elif state.pending_clarification is not None and (answer := _clarification_answer(text)) is not None:
            plan = self._clarified(state, snapshot, answer)[0]   # "הרכב שלי היום" / "חלופה לבדוק"
        else:
            state.pending_confirmation = None    # תשובה אחרת: השאלה הקודמת כבר לא רלוונטית
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
        return self._finish(ctx, history, text, plan, before, record_run, und=und)

    @staticmethod
    def _start_correction(ctx: ToolContext) -> Stage:
        """העובד מתקן את הרכב בזמן המתנה: השאלות על הרכב הקודם מתבטלות, ונשאל שוב על הרכב החדש."""
        state = ctx.state
        ctx.cancel_question_ids.extend(state.pending_rep.question_ids)
        state.pending_rep = None
        state.current.copay_unknown = state.current.use_value_unknown = False
        return next_stage(state)

    def _finish(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str, plan: ParserPlan,
                before: Stage, record_run: RecordRun, choice: bool = False,
                und: Optional[Understanding] = None, extra: Sequence[str] = ()) -> TurnOutcome:
        """UNDERSTAND (parser + one LLM call) → DECIDE (code: save, then a reply plan) → SAY (the LLM
        writes the plan; the template renders it when the LLM is off, fails or breaks a check)."""
        state, snapshot = ctx.state, ctx.snapshot
        und = und or Understanding(remainder=text)
        car_before = _car_key(state)
        before_turn = _Before.of(state)
        used_llm = False
        deadline = self.llm.turn_deadline()   # אחרי LLM_TURN_DEADLINE_S — תבניות, בלי ספקים נוספים
        conflicts: list[str] = []
        parser_decides = plan.complete and (self.mode == "parser_first" or plan.calls == [] or plan.direct)
        if parser_decides or not self.llm.available:
            path = "choice" if choice else "parser" if plan.complete else "templates"
            # a direct answer to the code's own question ("לא" to a confirmation) is understood
            understood = self._execute(ctx, plan.calls) or plan.notice is not None or (plan.direct and plan.complete)
            if plan.clarify is not None:
                state.pending_clarification = plan.clarify     # "<רכב> הוא הרכב שלך היום, או חלופה?"
                state.role_asked.append(plan.clarify.catalog_code)
                understood = True
        else:
            # המודל קורא קודם. תוכנית הקורא חושבה לפני כל שינוי במצב: גיבוי, ובדיקה צולבת כשנקרא במלואו.
            cross = _expected_values(snapshot, plan) if plan.complete else {}
            try:
                understood = self._extract_llm(ctx, history, text, deadline, cross, conflicts, und)
                used_llm, path = True, "llm"
            except LLMError:
                log.warning("llm extraction failed; falling back to the parser")
                understood, path = False, "fallback"
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
                # labelled values the LLM left out ("משלמת עליה 300" next to the car) — never dropped
                understood = self._fill_gaps(ctx, und.remainder, conflicts) or understood

        if before_turn.group == "Q2" and any(f in Q2_FIELDS for f in before_turn.changed(state)):
            state.q2_answered = True             # Q2 answered in part: its leftovers wait for Q3
        state.unanswered_question = und.unanswered[0] if und.unanswered else None
        self._open_blocking_rep(ctx)
        self._resolve_rank(ctx)
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        state.stage = next_stage(state)
        understood = understood or state.stage != before
        said = (_plan_notice(plan, path) or "").startswith("רשמתי")
        confirmations = before_turn.confirmations(state, snapshot, just_calculated, said)

        # רכב שזוהה בתור הזה (מהקורא, מהמודל או מהגיבוי) — אומרים איזה, כדי שטעות לא תעבור בשקט
        matched_car = state.current.source in ("catalog", "tax") and _car_key(state) != car_before
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        problems = [conflict_text(c) for c in conflicts] + [range_problem_text(*p) for p in ctx.range_problems]
        answers, terms = self._answers(snapshot, und)
        lines = [*extra, *([UNANSWERED] if und.unanswered else []), *([snapshot.faq.redirect] if und.redirect else [])]
        reply_plan = plan_reply(
            state, snapshot, understood=understood or bool(problems) or und.anything, just_calculated=just_calculated,
            matched_car=matched_car, notice="\n".join(problems) or _plan_notice(plan, path), rep_opened=rep_opened,
            notice_asks=bool(problems), confirmations=confirmations, answers=list(zip(answers, terms)),
            follow_up=und.presence, explain=und.help, extra=lines)
        _mark_asked(state)
        # the reply about unknown fields alternates its wording: "לא יודע" twice never gets the same text
        state.unknown_told = state.unknown_told + 1 if current_group(state)[0] == "unknown" else 0
        state.misses = 0 if (understood or problems or und.anything) else state.misses + 1
        state.last_asked = [] if just_calculated else list(current_group(state)[1])
        requests = dict(question_requests=ctx.question_requests, cancel_question_ids=ctx.cancel_question_ids)
        draft = reply_plan.text()
        # results and the rep messages keep their exact template text; buttons get the template (fast)
        if choice or reply_plan.fixed or rep_opened or not self.llm.available or self.mode == "parser_first":
            return TurnOutcome(reply=draft, source="template", just_calculated=just_calculated, path=path,
                               **requests)
        reply, source, blocked, say = self._say(reply_plan, deadline)
        return TurnOutcome(reply=reply, source=source, guard_blocked=blocked, just_calculated=just_calculated,
                           path=path, say=say, **requests)

    @staticmethod
    def _answers(snapshot: RulesSnapshot, und: Understanding) -> tuple[list[str], list[str]]:
        entries = [e for e in (snapshot.faq.get(i) for i in und.faq_ids) if e is not None]
        return [e.answer for e in entries], [e.term for e in entries]

    def _fill_gaps(self, ctx: ToolContext, text: str, conflicts: Sequence[str] = ()) -> bool:
        """Labelled values the parser reads with certainty ("פנסיה 6%", "משלמת 300") that no tool saved
        this turn: saved now, so nothing the employee said is dropped."""
        if not text:
            return False
        state, ext = ctx.state, ctx.snapshot.extensions
        reading = read_profile(text, ctx.snapshot.occupations.keys(), ext.correction_cues, ext.value_qualifiers)
        p = state.profile
        current = {"base_salary": p.base, "commissions": p.commissions, "bonus": p.bonus,
                   "pension_percent": p.pension_percent, "study_fund_percent": p.study_fund_percent,
                   "credit_points": p.credit_points, "children_under_23": p.children}
        # labelled values ("פנסיה 6%") and explicit zeros ("אין לי ילדים") — never a bare number
        said = {**{k: v for k, v in reading.fields.items() if _is_zero(v)}, **reading.labelled}
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

    def _system(self, ctx: ToolContext) -> ChatMessage:
        state, snap = ctx.state, ctx.snapshot
        stage = next_stage(state)
        group, missing = current_group(state)
        context = ""
        if group in ("Q1", "Q2") or "opt_out_occupation" in missing:
            context = "Occupations table: " + "; ".join(snap.occupations.keys())
        elif stage == Stage.COLLECT_CURRENT_CAR and state.current.choices:
            context = "Car choices: " + "; ".join(f"{i}. {c.label}" for i, c in enumerate(state.current.choices, 1))
        if "options" in missing or stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
            context += ("\n" if context else "") + "Options: " + "; ".join(
                f"{o.id}: {o.label}" for o in available_options(snap, state))
        return ChatMessage("system", EXTRACT_PROMPT.format(stage=stage.value, missing=", ".join(missing) or "nothing",
                                                           context=context))

    def _extract_llm(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str,
                     deadline: Optional[float] = None, cross: Optional[dict[str, Any]] = None,
                     conflicts: Optional[list[str]] = None, und: Optional[Understanding] = None) -> bool:
        """UNDERSTAND: ONE call. The model returns every part of the message at once: the data tools for
        values (save_profile, match_current_car, calculate / rank_options) and note_message for questions,
        presence without an amount, help and off-topic. Code checks every call before it runs."""
        state, snap = ctx.state, ctx.snapshot
        und = und if und is not None else Understanding(remainder=text)
        names = _understand_tools(state)
        if not names:
            return False
        system = self._system(ctx)
        system = ChatMessage("system", system.content + NOTE_PROMPT.format(ids=", ".join(snap.faq.ids)))
        messages: list[ChatMessage] = [system, *history[-HISTORY_MESSAGES:], ChatMessage("user", text)]
        response = self.llm.complete(messages, [TOOL_SPECS[n] for n in names] + [note_spec(snap.faq)],
                                     require_tool=True, deadline=deadline)
        for call in response.tool_calls:
            if call.name == NOTE_TOOL:
                merge_note(und, call.args, snap.faq, text)
        if (und.question_only and und.anything) or und.redirect:
            return False                  # only questions / off topic: nothing is saved from this message
        understood = False
        order = {"save_profile": 0, "match_current_car": 1, "list_options": 2, "rank_options": 3, "calculate": 3}
        calls = sorted((c for c in response.tool_calls if c.name != NOTE_TOOL), key=lambda c: order.get(c.name, 9))
        for call in calls:
            call = self._check_call(ctx, call, text, und)
            if call is None:
                continue
            blocked = _role_conflict(ctx, call, text)
            if blocked is not None:
                log.info("tool %s blocked: the car's role contradicts the message", call.name)
                if blocked.catalog_code in state.role_asked:
                    return understood    # כבר שאלנו על הרכב: לא שואלים שוב — הקורא פועל לפי המילים
                state.pending_clarification = CarClarification(catalog_code=blocked.catalog_code,
                                                               label=blocked.label)
                state.role_asked.append(blocked.catalog_code)
                return understood        # לא ממשיכים עם ההודעה הזו: שואלים על הרכב
            call = _cross_check(ctx, call, cross or {}, conflicts if conflicts is not None else [])
            understood = _recorded(self._run(ctx, call, relaxed=True)) or understood
        return understood

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
            return ToolCall(call.name, args, call.id)
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
        return any([_recorded(self._run(ctx, call)) for call in calls])

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
        calls = self._llm_waiting_correction(state, text)
        if calls:
            return ParserPlan(list(calls), True, direct=True), ""
        # בלי מודל, או שהמודל לא בטוח: לא מנחשים — שואלים, וזוכרים על איזה רכב
        state.pending_clarification = CarClarification(catalog_code=others[0].catalog_code, label=others[0].label)
        return None, clarify_car_text(others[0].label)

    @staticmethod
    def _clarified(state: ConversationState, snapshot: RulesSnapshot,
                   answer: str) -> tuple[Optional[ParserPlan], str]:
        """תשובה ל"הוא הרכב שלך היום, או חלופה?": mine → התאמה (מחדש); alternative → בהמתנה לנציג
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

    def _llm_waiting_correction(self, state: ConversationState, text: str) -> Optional[tuple[ToolCall, ...]]:
        """המודל מקבל רק את match_current_car: יקרא לו רק אם ההודעה מתקנת את הרכב של היום."""
        if not self.llm.available:
            return None
        system = WAITING_CORRECTION_PROMPT.format(car=state.current.label or "")
        try:
            response = self.llm.complete([ChatMessage("system", system), ChatMessage("user", text)],
                                         [TOOL_SPECS["match_current_car"]], deadline=self.llm.turn_deadline())
        except LLMError:
            return None
        calls = tuple(c for c in response.tool_calls if c.name == "match_current_car")
        return calls or None

    @staticmethod
    def _request(ctx: ToolContext, field: MissingField, mode: Mode, answer_type: AnswerType, text: str,
                 context: dict[str, Optional[str]], suggested: Optional[str] = None, option_id: str = "") -> None:
        """שאלה לנציג. ההקשר: רק מה שהנציג צריך (קבוצה, רכב, סכום מוצע, עיסוק) — בלי שכר."""
        clean = {k: v for k, v in context.items() if v is not None}
        ctx.question_requests.append(QuestionRequest(field, mode, answer_type, text, clean, suggested, option_id))

    @staticmethod
    def _resolve_rank(ctx: ToolContext) -> None:
        """"הכי משתלם" שנרשם לפני שהפרטים היו ידועים: עכשיו, כשרק החלופות חסרות, הבחירה נעשית."""
        state = ctx.state
        if state.rank_requested and not state.selected and next_stage(state) == Stage.CHOOSE_OPTIONS:
            if not run_tool(ctx, "rank_options", {}).get("ok"):
                state.rank_requested = False     # אין חלופות זכאיות לדירוג — שואלים על החלופות

    def _open_blocking_rep(self, ctx: ToolContext) -> None:
        """שאלה לנציג רק אחרי שהפרופיל מלא: קודם משלימים את מה שהעובד יודע."""
        state = ctx.state
        if state.pending_rep is not None or open_profile(state.profile):
            return
        reason = blocking_rep_reason(state)
        if reason is None:
            return
        p, c = state.profile, state.current
        if reason == RepReason.CURRENT_COPAY:
            suggested = None
            if c.catalog_code:
                car = next(x for x in ctx.snapshot.catalog if x.catalog_code == c.catalog_code)
                suggested = str(car.default_copay)
            text = f"מה ההשתתפות החודשית הנוכחית של העובד ב-{c.label}?" + (
                f" לפי הקטלוג: {suggested} ₪" if suggested else "")
            self._request(ctx, MissingField.CURRENT_COPAY, Mode.REQUIRED, AnswerType.MONEY, text,
                          {"group": p.group, "car_label": c.label, "catalog_code": c.catalog_code,
                           "suggested_amount": suggested}, suggested)
        else:
            self._request(ctx, MissingField.CURRENT_USE_VALUE, Mode.REQUIRED, AnswerType.MONEY,
                          f"מה שווי השימוש החודשי של הרכב הנוכחי של העובד ({c.label})?",
                          {"group": p.group, "car_label": c.label})
        state.pending_rep = PendingRep(reason=reason, resume_stage=Stage.COLLECT_CURRENT_CAR,
                                       labels=[c.label or ""])

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
        amount = (Decimal(event.value) if field not in (MissingField.OCCUPATION_OPT_OUT, MissingField.GENERAL_QUESTION)
                  else None)

        if field == MissingField.NEW_CAR_COPAY:
            default = next((c.default_copay for c in snapshot.catalog if c.catalog_code == event.option_id), None)
            state.confirmed_copays[event.option_id] = ConfirmedCopay(
                value=amount, effective_date=event.effective_date, changed=default is None or amount != default)
            line = self._apply_new_car_copay(ctx, event, amount, record_run)
            if line:
                lines.append(line)
            show_results = was_waiting_on_copay and state.pending_rep is None   # REP_REQUIRED: עכשיו מציגים
        elif field in (MissingField.CURRENT_COPAY, MissingField.CURRENT_USE_VALUE):
            c = state.current
            if field == MissingField.CURRENT_COPAY:
                c.copay, c.copay_unknown = amount, False
            else:
                c.source, c.label = "stated", c.label or c.query
                c.use_value, c.use_value_unknown = amount, False
            state.calculated_for = []
            lines.append(rep_current_text(field.value, c.label or "", amount))
        elif field == MissingField.OCCUPATION_OPT_OUT:
            allowed = event.value == "true"
            state.profile.rep_opt_out_allowed = allowed
            state.calculated_for = []
            lines.append(rep_opt_out_text(allowed))
        elif field == MissingField.MAINTENANCE_AMOUNT:
            state.profile.rep_opt_out_maintenance = amount
            state.calculated_for = []
            lines.append(rep_maintenance_text(amount))
        elif field == MissingField.GENERAL_QUESTION:
            # the representative's own words; nothing in the state changes
            lines.append(f"נציג התחבורה ענה על השאלה שלך:\n{event.value}")

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
                           path="rep", question_requests=ctx.question_requests)

    def _apply_new_car_copay(self, ctx: ToolContext, event: QuestionAnswered, amount: Decimal,
                             record_run: RecordRun) -> Optional[str]:
        """אותו סכום — האישור נרשם בהנחות, בלי חישוב. סכום אחר — המנוע מחשב את החלופה מחדש."""
        state, snap = ctx.state, ctx.snapshot
        item = next((r for r in state.results if r.option_id == event.option_id), None)
        if item is None:
            return None                        # החלופה כבר לא מוצגת; ההשתתפות נשמרה לפעם הבאה
        used = (item.result.get("alternative") or {}).get("copay")
        if used is not None and Decimal(str(used)) == amount:
            pending_text = f"השתתפות ב-{item.label} לפי הקטלוג — ממתינה לאישור נציג"
            item.result["assumptions"] = [
                confirmed_assumption(item.label, event.effective_date) if a == pending_text else a
                for a in item.result.get("assumptions", [])]
            item.result["copay_pending"] = False
            return rep_confirmed_text(item.label, event.effective_date)
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
        item.result = _dump(result)
        if old_delta is None or result.delta_display is None:
            return rep_confirmed_text(item.label, event.effective_date)
        return rep_changed_text(item.label, amount, event.effective_date, old_delta, result.delta_display)

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
            confirmed = state.confirmed_copays.get(option_id)
            alternative = option_state(option, policy, confirmed.value if confirmed else None)
            result = _with_assumptions(evaluate(employee, current, alternative, snap.calc, occupation), state,
                                       option_id, option.label)
            if option.car is None and state.profile.rep_opt_out_allowed is False:
                result = result.model_copy(update={"reason": OPT_OUT_DENIED})   # לפי תשובת הנציג
            inputs = {
                "employee": employee.model_dump(mode="json"),
                "current": current.model_dump(mode="json"),
                "alternative": alternative.model_dump(mode="json"),
                "occupation": occupation.model_dump(mode="json") if occupation else None,
            }
            run_id = record_run(option.label, inputs, result)
            results.append(OptionResult(option_id=option_id, label=option.label, run_id=run_id,
                                        result=_dump(result)))
            needs_opt_out = needs_opt_out or "OCCUPATION_OPT_OUT" in result.missing_fields
            pending_copay = getattr(alternative, "copay_status", None) == CopayStatus.CATALOG_DEFAULT_PENDING
            if option.car is not None and pending_copay and result.status != CalcStatus.NOT_ELIGIBLE:
                # השתתפות לפי הקטלוג: הנציג מאשר (CONFIRM, התוצאה מוצגת) או חייב לענות (REP_REQUIRED)
                mode = Mode.REQUIRED if policy == "REP_REQUIRED" else Mode.CONFIRM
                default = str(option.car.default_copay)
                self._request(ctx, MissingField.NEW_CAR_COPAY, mode, AnswerType.MONEY,
                              f"מה ההשתתפות החודשית של העובד ב-{option.label}? לפי הקטלוג: {default} ₪",
                              {"group": state.profile.group, "car_label": option.label,
                               "catalog_code": option.car.catalog_code, "suggested_amount": default},
                              default, option.id)
                if mode == Mode.REQUIRED:
                    required_labels.append(option.label)
        state.results = results
        state.calculated_for = list(state.selected)
        if required_labels and state.pending_rep is None:
            state.pending_rep = PendingRep(reason=RepReason.NEW_CAR_COPAY, resume_stage=Stage.PRESENTED,
                                           labels=required_labels)
        if needs_opt_out:
            p = state.profile
            if p.rep_opt_out_allowed is None:
                self._request(ctx, MissingField.OCCUPATION_OPT_OUT, Mode.REQUIRED, AnswerType.BOOLEAN,
                              "האם עובד בעיסוק הזה רשאי לוותר על רכב?",
                              {"group": p.group, "occupation_text": p.occupation_text})
                reason = RepReason.OCCUPATION_OPT_OUT
            else:   # מותר לוותר, וחסר רק סכום האחזקה
                self._request(ctx, MissingField.MAINTENANCE_AMOUNT, Mode.REQUIRED, AnswerType.MONEY,
                              "מה סכום אחזקת הרכב החודשי לעובד בעיסוק הזה אם יוותר על הרכב?",
                              {"group": p.group, "occupation_text": p.occupation_text})
                reason = RepReason.MAINTENANCE_AMOUNT
            if state.pending_rep is None:
                state.pending_rep = PendingRep(reason=reason, resume_stage=Stage.PRESENTED)

    # ---------- ניסוח ----------

    def _say(self, plan: ReplyPlan, deadline: Optional[float] = None) -> tuple[str, str, bool, str]:
        """SAY: the LLM writes the plan in natural Hebrew. Rejected (→ the template of the same plan) when
        it adds a number that is not in the plan, drops a number or an answered term, or does not end with
        the call to action."""
        draft = plan.text()
        if deadline is not None and deadline - self.llm.clock() < SAY_RESERVE_S:
            return draft, "template", False, "deadline"     # no time left in the turn budget
        try:
            response = self.llm.complete([ChatMessage("system", SAY_PROMPT),
                                          ChatMessage("user", json.dumps(plan.as_json(), ensure_ascii=False))],
                                         deadline=deadline)
        except LLMError as exc:
            return draft, "template", False, "deadline" if getattr(exc, "timeout", False) or "זמן" in str(exc) else "outage"
        reply = response.text.strip()
        if not reply:
            return draft, "template", False, "empty"
        if violations(reply, collect_numbers(draft)):
            log.warning("number guard blocked an llm reply")
            return draft, "template", True, "guard"
        missing = uncovered(reply, plan)
        if missing:
            log.warning("llm reply did not cover the plan (%s); template used", missing)
            return draft, "template", False, f"coverage:{missing}"
        return reply, "llm", False, "llm"

    # ---------- a question the FAQ does not cover → the transport representative ----------

    def _prepare_rep_question(self, ctx: ToolContext) -> TurnOutcome:
        """"לשאול את נציג התחבורה": the question is rewritten without amounts or personal numbers, checked
        in code, and shown to the employee before anything is sent."""
        state, snap = ctx.state, ctx.snapshot
        question, state.unanswered_question = state.unanswered_question, None
        text = self._rep_rephrase(snap, question) if question else None
        if text is None:
            line = "כדי לשמור על הפרטיות, אשלח לנציג רק שאלה בלי סכומים ובלי מספרים אישיים. כתבו אותה שוב כך."
            plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[line])
        else:
            state.pending_rep_question = text
            plan = plan_reply(state, snap, understood=True, just_calculated=False)
        return TurnOutcome(reply=plan.text(), source="template", path="choice")

    def _rep_rephrase(self, snapshot: RulesSnapshot, question: str) -> Optional[str]:
        def snap_ok(text: str) -> bool:
            return rep_ok(snapshot, text)

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
        if send and text and rep_ok(snap, text):
            p, c = state.profile, state.current
            car = c.label if c.source in ("catalog", "tax", "stated") else None
            self._request(ctx, MissingField.GENERAL_QUESTION, Mode.CONFIRM, AnswerType.TEXT, text,
                          {"group": p.group, "car_label": car}, option_id=f"gq-{secrets.token_hex(6)}")
            line = "שלחתי את השאלה לנציג התחבורה. התשובה תגיע לכאן, ובינתיים אפשר להמשיך."
        else:
            line = "בסדר, לא שלחתי."
        plan = plan_reply(state, snap, understood=True, just_calculated=False, confirmations=[line])
        return TurnOutcome(reply=plan.text(), source="template", path="choice",
                           question_requests=ctx.question_requests)


def rep_ok(snapshot: RulesSnapshot, text: str) -> bool:
    """A car name may hold digits ("אטו 3", "MG4"): only those and model years may stay."""
    cleaned = text
    for car in snapshot.catalog:
        for name in sorted(catalog_names_of(car), key=len, reverse=True):
            cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
    return bool(text.strip()) and rep_text_ok(cleaned)


def _norm_line(text: str) -> str:
    return re.sub(r"[\s\"'״׳“”*]+", "", text)


def uncovered(reply: str, plan: ReplyPlan) -> Optional[str]:
    """What the phrased reply leaves out of the plan ("cta" / "items" / "terms" / "numbers"), or None: every
    number, every answered FAQ term, a line for every plan item, and the call to action as its last line."""
    from .parsing import numbers_in

    lines = [line for line in reply.splitlines() if line.strip()]
    if plan.cta and (not lines or _norm_line(lines[-1]) != _norm_line(plan.cta)):
        return "cta"
    # every plan item (answer, ack, follow-up, the question) keeps at least one line before the CTA
    items = len(plan.answers) + len(plan.ack) + len(plan.follow_up) + len(plan.ask)
    if len(lines) - (1 if plan.cta else 0) < items:
        return "items"
    if any(term not in reply for term in plan.terms):
        return "terms"
    have = {abs(n) for n in numbers_in(reply)}
    return None if all(abs(n) in have for n in numbers_in(plan.text())) else "numbers"


def covers(reply: str, plan: ReplyPlan) -> bool:
    return uncovered(reply, plan) is None