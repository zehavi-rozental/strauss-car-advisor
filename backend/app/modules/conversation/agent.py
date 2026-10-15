"""תור אחד בשיחה: חילוץ → כלים → קידום המכונה → חישוב → ניסוח → שומר מספרים.

הקוד קובע את השלב ומחשב; מודל השפה (אם יש) רק מחלץ ומנסח.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from app.modules.calculation.public import CopayStatus, ScenarioResult, evaluate
from app.modules.calculation.public import Status as CalcStatus
from app.modules.cases.public import AnswerType, MissingField, Mode, QuestionAnswered, QuestionRequest
from app.modules.rules.public import RulesSnapshot, find_catalog_car, words


from .fallback import ParserPlan, correction_plan, fallback_plan, named_cars
from .guard import collect_numbers, violations
from .llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from .parsing import match_occupation, redact_id_numbers
from .stage import (
    ALLOWED_TOOLS,
    MAX_OPTIONS,
    CarClarification,
    ConfirmedCopay,
    ConversationState,
    OptionResult,
    PendingRep,
    RepReason,
    Stage,
    blocking_rep_reason,
    missing_current_car,
    missing_profile,
    next_stage,
)
from .templates import (
    GREETING,
    OPT_OUT_DENIED,
    approximate_assumptions,
    clarify_car_text,
    confirmed_assumption,
    question,
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
)
from .tools import (
    TOOL_SPECS,
    ToolContext,
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
- Never guess. Never expand a shortened number: for a salary, "18" or "בערך 18" is NOT 18,000. Pass the number
  exactly as written; the system checks it and asks the employee to confirm.
- If the employee gives a value as an estimate (כ-, בערך, בסביבות, בממוצע, משתנות), pass it and list that
  field's name in approximate_fields."""

# תוצאות: המודל כותב רק פתיח בלי מספרים. שורות התוצאה (נטו היום, נטו בחלופה, ההפרש עם סימן)
# תמיד מהתבנית — כך שהמודל לא יכול לטעות בכיוון ("ירד" כשהנטו עולה) או במספר.
INTRO_PROMPT = """Write one or two short, warm sentences in Hebrew to an employee. The system shows a
comparison of their monthly net pay right after your text.
Do not write any digits, amounts, percentages or car model names.
Do not say whether the net pay goes up or down, or whether the change is good or bad.
Address the employee in gender-neutral Hebrew (plural forms such as כתבו, or neutral phrasing); never assume their gender.
Return only the sentences."""

DIGIT_RE = re.compile(r"\d")

PHRASE_PROMPT = """Rewrite the draft below as one friendly reply in Hebrew to the employee.
Keep every number, amount and list item exactly as in the draft.
Do not add numbers, estimates, calculations or advice that are not in the draft.
Address the employee in gender-neutral Hebrew (plural forms such as כתבו, or neutral phrasing); never assume their gender.
Return only the reply text."""


RecordRun = Callable[[str, dict[str, Any], ScenarioResult], str]


@dataclass
class TurnOutcome:
    reply: str
    source: str               # llm / template
    guard_blocked: bool = False
    just_calculated: bool = False
    path: str = "parser"      # parser / llm / fallback / templates / waiting — ללוג, בלי תוכן
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


def _with_assumptions(result: Any, state: ConversationState, option_id: str, label: str) -> Any:
    """הנחות שהמנוע לא יודע עליהן: ערכים משוערים, והשתתפות שהנציג אישר או קבע."""
    extra = approximate_assumptions(state)
    confirmed = state.confirmed_copays.get(option_id)
    if confirmed is not None:
        extra.append(rep_amount_assumption(label, confirmed.value, confirmed.effective_date) if confirmed.changed
                     else confirmed_assumption(label, confirmed.effective_date))
    return result.model_copy(update={"assumptions": result.assumptions + extra}) if extra else result


_YES = {"כן", "נכון", "בדיוק", "אכן", "yes"}
_NO = {"לא", "no"}


def _confirmation_plan(state: ConversationState, text: str) -> Optional[ParserPlan]:
    """תשובה ל"התכוונת ל-18,000 ₪?": כן → נשמר; לא → שואלים שוב את השדה; אחר → None."""
    ws = words(text)
    pending = state.pending_confirmation
    if not ws or len(ws) > 3 or pending is None:
        return None
    if ws[0] in _YES:
        state.pending_confirmation = None
        args = {pending.field: str(pending.value),
                "approximate_fields": [pending.field] if pending.approximate else []}
        return ParserPlan([ToolCall("save_profile", args, "confirmed")], True, direct=True)
    if ws[0] in _NO:
        state.pending_confirmation = None
        return ParserPlan(complete=True, direct=True)
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


def _plan_notice(plan: ParserPlan, path: str) -> Optional[str]:
    """משפט מהקורא: כשפעל (parser), או כשהקריאות שלו רצו כגיבוי (fallback / templates)."""
    if path == "parser":
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


class Agent:
    def __init__(self, llm: LLMChain, mode: str = "llm_first"):
        self.llm = llm
        self.mode = mode          # llm_first | parser_first

    def greeting(self, state: ConversationState, snapshot: RulesSnapshot) -> str:
        return GREETING + question("occupation", state, snapshot)

    def run_turn(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        before = next_stage(state)

        if before == Stage.WAITING_FOR_REP:
            correction, reply = self._waiting(state, snapshot, text)
            if correction is None:
                # ממתינים לנציג: תשובה קצרה (או שאלת הבהרה), בלי שינוי בשלב, ברכב או בשאלות
                return TurnOutcome(reply=reply, source="template", path="waiting")
            # העובד מתקן את הרכב: השאלות על הרכב הקודם מתבטלות, ונשאל שוב על הרכב החדש
            ctx.cancel_question_ids.extend(state.pending_rep.question_ids)
            state.pending_rep = None
            state.current.copay_unknown = state.current.use_value_unknown = False
            before = next_stage(state)
            plan = correction
        elif state.pending_confirmation is not None and (confirmed := _confirmation_plan(state, text)) is not None:
            plan = confirmed                     # "כן" / "לא" על "התכוונת ל-18,000 ₪?"
        else:
            state.pending_confirmation = None    # תשובה אחרת: השאלה הקודמת כבר לא רלוונטית
            # הקורא הדטרמיניסטי קודם. מה שהוא קורא במלואו לא מגיע למודל בכלל — מהיר ובלי תלות בספק.
            plan = fallback_plan(state, snapshot, text)
        car_before = _car_key(state)
        used_llm = False
        deadline = self.llm.turn_deadline()   # אחרי LLM_TURN_DEADLINE_S — תבניות, בלי ספקים נוספים
        conflicts: list[str] = []
        parser_decides = plan.complete and (self.mode == "parser_first" or plan.calls == [] or plan.direct)
        if parser_decides or not self.llm.available:
            path = "parser" if plan.complete else "templates"
            understood = self._execute(ctx, plan.calls) or plan.notice is not None
        else:
            # המודל קורא קודם. תוכנית הקורא חושבה לפני כל שינוי במצב: גיבוי, ובדיקה צולבת כשנקרא במלואו.
            cross = _expected_values(snapshot, plan) if plan.complete else {}
            try:
                understood = self._extract_llm(ctx, history, text, deadline, cross, conflicts)
                used_llm, path = True, "llm"
            except LLMError:
                log.warning("llm extraction failed; falling back to the parser")
                understood, path = False, "fallback"
            # המודל לא קרא לכלי, נכשל או בהמתנה: הקורא (מלא, או רק הגיבוי הבטוח שלו). ניחוש "כל ההודעה
            # היא הערך" רץ רק כשהמודל לא זמין — אם המודל קרא את ההודעה ולא רשם דבר, לא מנחשים.
            declined = path == "llm"
            if not understood and not conflicts and not (declined and plan.guess):
                if plan.calls or plan.notice is not None or not declined:
                    path = "fallback"            # הקורא פעל, או שהמודל נכשל
                understood = self._execute(ctx, plan.calls) or plan.notice is not None

        self._open_blocking_rep(ctx)
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        state.stage = next_stage(state)
        understood = understood or state.stage != before

        # רכב שזוהה בתור הזה (מהקורא, מהמודל או מהגיבוי) — אומרים איזה, כדי שטעות לא תעבור בשקט
        matched_car = state.current.source in ("catalog", "tax") and _car_key(state) != car_before
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        problems = [conflict_text(c) for c in conflicts] + [range_problem_text(*p) for p in ctx.range_problems]
        draft = render(state, snapshot, understood=understood or bool(problems), just_calculated=just_calculated,
                       matched_car=matched_car, notice="\n".join(problems) or _plan_notice(plan, path),
                       rep_opened=rep_opened, notice_asks=bool(problems))
        requests = dict(question_requests=ctx.question_requests, cancel_question_ids=ctx.cancel_question_ids)
        # ניסוח במודל רק לתוצאות או להודעה שלא הובנה (שאלה חופשית). השאלה הבאה בפרופיל/ברכב
        # היא תבנית: מהירה, ובלי סיכון לשומר המספרים.
        checked = bool(problems) or state.pending_confirmation is not None   # בדיקת ערכים — תמיד תבנית
        if not used_llm or not (just_calculated or not understood) or rep_opened or checked:
            return TurnOutcome(reply=draft, source="template", just_calculated=just_calculated, path=path,
                               **requests)
        if just_calculated:
            # לפתיח יש תקציב קצר משלו: אם לא ענה בזמן — התוצאה מהתבנית מיד, בלי פתיח
            reply, source, blocked = self._intro(draft, text, self.llm.intro_deadline(deadline))
        else:
            reply, source, blocked = self._phrase(state, draft, text, deadline)
        return TurnOutcome(reply=reply, source=source, guard_blocked=blocked, just_calculated=just_calculated,
                           path=path, **requests)

    # ---------- חילוץ ----------

    def _system(self, ctx: ToolContext) -> ChatMessage:
        state, snap = ctx.state, ctx.snapshot
        stage = next_stage(state)
        missing = missing_profile(state.profile) + missing_current_car(state.current)
        context = ""
        if stage == Stage.COLLECT_PROFILE:
            context = "Occupations table: " + "; ".join(snap.occupations.keys())
        elif stage == Stage.COLLECT_CURRENT_CAR and state.current.choices:
            context = "Car choices: " + "; ".join(f"{i}. {c.label}" for i, c in enumerate(state.current.choices, 1))
        elif stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
            context = "Options: " + "; ".join(f"{o.id}: {o.label}" for o in available_options(snap, state))
        return ChatMessage("system", EXTRACT_PROMPT.format(stage=stage.value, missing=", ".join(missing) or "nothing",
                                                           context=context))

    def _extract_llm(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str,
                     deadline: Optional[float] = None, cross: Optional[dict[str, Any]] = None,
                     conflicts: Optional[list[str]] = None) -> bool:
        messages: list[ChatMessage] = [self._system(ctx), *history[-HISTORY_MESSAGES:], ChatMessage("user", text)]
        understood = False
        for round_no in range(MAX_TOOL_ROUNDS):
            stage = next_stage(ctx.state)
            names = ALLOWED_TOOLS[stage]
            if not names:
                break
            messages[0] = self._system(ctx)
            forced = round_no == 0 and stage in FORCED_TOOL_STAGES
            try:
                response = self.llm.complete(messages, [TOOL_SPECS[n] for n in names], require_tool=forced,
                                             deadline=deadline)
            except LLMError:
                if round_no == 0:
                    raise          # שום דבר לא נשמר — מצב הגיבוי יקרא את ההודעה
                # סבב מאוחר נכשל (למשל 429): מה שכבר נשמר נשאר. אסור לקרוא שוב את אותה
                # הודעה בגיבוי — השלב כבר התקדם והיא הייתה מתפרשת כתשובה לשאלה אחרת.
                log.warning("llm extraction round %d failed; keeping what was saved", round_no + 1)
                break
            if not response.tool_calls:
                break
            messages.append(ChatMessage("assistant", response.text, tool_calls=response.tool_calls,
                                        raw=response.raw))
            for call in response.tool_calls:
                call = _cross_check(ctx, call, cross or {}, conflicts if conflicts is not None else [])
                facts = self._run(ctx, call)
                understood = understood or _recorded(facts)
                messages.append(ChatMessage("tool", json.dumps(facts, ensure_ascii=False, default=str),
                                            tool_call_id=call.id, name=call.name))
            # סבב נוסף רק כשהשלב התקדם לשלב שעדיין אוסף נתונים (פרופיל ורכב באותה הודעה).
            # רכב שנמצא → בחירת חלופות: העובד עוד לא בחר, וסבב נוסף הוא קריאה מבוזבזת.
            new_stage = next_stage(ctx.state)
            if new_stage == stage or new_stage not in FORCED_TOOL_STAGES:
                break
        return understood

    def _run(self, ctx: ToolContext, call: ToolCall) -> dict[str, Any]:
        """רק הכלים שהשלב הנוכחי מתיר. כלי אחר מתועד ונדחה."""
        if call.name not in ALLOWED_TOOLS[next_stage(ctx.state)]:
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

        clarification = state.pending_clarification
        if clarification is not None:
            answer = _clarification_answer(text)
            if answer == "mine":
                state.pending_clarification = None
                car = next(c for c in snapshot.catalog if c.catalog_code == clarification.catalog_code)
                call = ToolCall("match_current_car", {"model": car.model_name}, "clarified")
                return ParserPlan([call], True, direct=True), ""
            if answer == "alternative":
                state.pending_clarification = None
                if clarification.catalog_code not in state.queued_options:
                    state.queued_options.append(clarification.catalog_code)
                return None, queued_option_text(clarification.label)

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

    def _open_blocking_rep(self, ctx: ToolContext) -> None:
        state = ctx.state
        if state.pending_rep is not None:
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
        amount = Decimal(event.value) if field != MissingField.OCCUPATION_OPT_OUT else None

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

        state.applied_answers.append(qid)
        if state.pending_rep is None and state.queued_options:
            # חלופות שהעובד ביקש בזמן ההמתנה — מחושבות עכשיו
            available = {o.id for o in available_options(snapshot, state)}
            queued = [o for o in state.queued_options if o in available][:MAX_OPTIONS]
            state.queued_options = []
            if queued:
                state.selected, state.calculated_for = queued, []
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        state.stage = next_stage(state)
        rep_opened = any(r.mode == Mode.REQUIRED for r in ctx.question_requests)
        if field == MissingField.NEW_CAR_COPAY:
            if show_results and state.results:
                lines.append(results_text(state))
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
        item.result = result.model_dump(mode="json")
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
                                        result=result.model_dump(mode="json")))
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

    def _intro(self, draft: str, text: str, deadline: Optional[float]) -> tuple[str, str, bool]:
        """פתיח מהמודל + שורות התוצאה מהתבנית. ספרה כלשהי בפתיח → בלי פתיח (נחסם)."""
        try:
            response = self.llm.complete([ChatMessage("system", INTRO_PROMPT),
                                          ChatMessage("user", f"Employee's message:\n{text}")], deadline=deadline)
        except LLMError:
            return draft, "template", False
        intro = response.text.strip()
        if not intro:
            return draft, "template", False
        if DIGIT_RE.search(intro):
            log.warning("number guard dropped an llm intro to results (digits)")
            return draft, "template", True
        return f"{intro}\n\n{draft}", "llm", False

    def _phrase(self, state: ConversationState, draft: str, text: str,
                deadline: Optional[float] = None) -> tuple[str, str, bool]:
        # מותר: מה שבמצב (פרופיל + תוצאות המנוע), מה שבטיוטה, ומה שהעובד עצמו כתב (ציטוט שלו)
        allowed = (collect_numbers(state.model_dump(mode="json")) | collect_numbers(draft)
                   | collect_numbers(text))
        try:
            response = self.llm.complete([ChatMessage("system", PHRASE_PROMPT),
                                          ChatMessage("user", f"Draft:\n{draft}\n\nEmployee's message:\n{text}")],
                                         deadline=deadline)
        except LLMError:
            return draft, "template", False
        reply = response.text.strip()
        if not reply:
            return draft, "template", False
        bad = violations(reply, allowed)
        if bad:
            log.warning("number guard blocked an llm reply (%d unknown numbers)", len(bad))
            return draft, "template", True
        return reply, "llm", False
