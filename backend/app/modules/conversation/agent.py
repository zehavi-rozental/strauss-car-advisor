"""תור אחד בשיחה: חילוץ → כלים → קידום המכונה → חישוב → ניסוח → שומר מספרים.

הקוד קובע את השלב ומחשב; מודל השפה (אם יש) רק מחלץ ומנסח.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from app.modules.calculation.public import CopayStatus, ScenarioResult, evaluate
from app.modules.calculation.public import Status as CalcStatus
from app.modules.cases.public import AnswerType, MissingField, Mode, QuestionRequest
from app.modules.rules.public import RulesSnapshot

from .fallback import correction_plan, fallback_plan
from .guard import collect_numbers, violations
from .llm.port import ChatMessage, LLMChain, LLMError, ToolCall
from .parsing import redact_id_numbers
from .stage import (
    ALLOWED_TOOLS,
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
from .templates import GREETING, question, render
from .tools import TOOL_SPECS, ToolContext, VehicleLookup, available_options, engine_inputs, option_state, run_tool

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
- Car models in Latin letters as on the tax-authority list (e.g. "קורולה קרוס" -> "COROLLA CROSS")."""

# תוצאות: המודל כותב רק פתיח בלי מספרים. שורות התוצאה (נטו היום, נטו בחלופה, ההפרש עם סימן)
# תמיד מהתבנית — כך שהמודל לא יכול לטעות בכיוון ("ירד" כשהנטו עולה) או במספר.
INTRO_PROMPT = """Write one or two short, warm sentences in Hebrew to an employee. The system shows a
comparison of their monthly net pay right after your text.
Do not write any digits, amounts, percentages or car model names.
Do not say whether the net pay goes up or down, or whether the change is good or bad.
Return only the sentences."""

DIGIT_RE = re.compile(r"\d")

PHRASE_PROMPT = """Rewrite the draft below as one friendly reply in Hebrew to the employee.
Keep every number, amount and list item exactly as in the draft.
Do not add numbers, estimates, calculations or advice that are not in the draft.
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


_CORRECTION_CUE_RE = re.compile(r"\s*(?:לא|סליחה|טעות|בעצם|רגע|אופס)(?![א-ת])|\s*(?:זה|זו)\s+לא\s+הרכב")


def _car_key(state: ConversationState) -> tuple:
    c = state.current
    return (c.source, c.catalog_code, c.label, c.use_value)


def _recorded(facts: dict[str, Any]) -> bool:
    """הכלי רץ ושמר משהו. קריאה כפויה בלי שדות (save_profile ריק) אינה "הבנה"."""
    return bool(facts.get("ok")) and facts.get("saved") != []


class Agent:
    def __init__(self, llm: LLMChain):
        self.llm = llm

    def greeting(self, state: ConversationState, snapshot: RulesSnapshot) -> str:
        return GREETING + question("occupation", state, snapshot)

    def run_turn(self, snapshot: RulesSnapshot, find_vehicle: VehicleLookup, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, find_vehicle=find_vehicle, state=state)
        before = next_stage(state)

        if before == Stage.WAITING_FOR_REP:
            correction = self._waiting_correction(state, snapshot, text)
            if correction is None:
                # ממתינים לנציג: תשובה קצרה, בלי מודל שפה ובלי שינוי במצב
                return TurnOutcome(reply=render(state, snapshot, understood=True, just_calculated=False),
                                   source="template", path="waiting")
            # העובד מתקן את הרכב: השאלות על הרכב הקודם מתבטלות, ונשאל שוב על הרכב החדש
            ctx.cancel_question_ids.extend(state.pending_rep.question_ids)
            state.pending_rep = None
            state.current.copay_unknown = state.current.use_value_unknown = False
            before = next_stage(state)
            plan = correction
        else:
            # הקורא הדטרמיניסטי קודם. מה שהוא קורא במלואו לא מגיע למודל בכלל — מהיר ובלי תלות בספק.
            plan = fallback_plan(state, snapshot, text)
        car_before = _car_key(state)
        used_llm = False
        deadline = self.llm.turn_deadline()   # אחרי LLM_TURN_DEADLINE_S — תבניות, בלי ספקים נוספים
        if plan.complete or not self.llm.available:
            path = "parser" if plan.complete else "templates"
            understood = self._execute(ctx, plan.calls) or plan.notice is not None
        else:
            try:
                understood = self._extract_llm(ctx, history, text, deadline)
                used_llm, path = True, "llm"
            except LLMError:
                log.warning("llm extraction failed; falling back to the parser")
                path = "fallback"
                understood = self._execute(ctx, plan.calls)   # התוכנית חושבה לפני כל שינוי במצב

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
        draft = render(state, snapshot, understood=understood, just_calculated=just_calculated,
                       matched_car=matched_car, notice=plan.notice if path == "parser" else None,
                       rep_opened=rep_opened)
        requests = dict(question_requests=ctx.question_requests, cancel_question_ids=ctx.cancel_question_ids)
        # ניסוח במודל רק לתוצאות או להודעה שלא הובנה (שאלה חופשית). השאלה הבאה בפרופיל/ברכב
        # היא תבנית: מהירה, ובלי סיכון לשומר המספרים.
        if not used_llm or not (just_calculated or not understood) or rep_opened:
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
                     deadline: Optional[float] = None) -> bool:
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

    @staticmethod
    def _waiting_correction(state: ConversationState, snapshot: RulesSnapshot, text: str):
        """בזמן המתנה על פרטי הרכב הנוכחי, הודעה שהקורא קורא במלואה כתיקון הרכב — מטופלת."""
        pending = state.pending_rep
        if pending is None or pending.reason not in (RepReason.CURRENT_COPAY, RepReason.CURRENT_USE_VALUE):
            return None
        # בזמן המתנה שם רכב לבד ("ATTO 3") הוא כנראה שאלה על חלופה — תיקון רק עם סימן מפורש בתחילתו
        if not _CORRECTION_CUE_RE.match(text):
            return None
        plan = correction_plan(state, snapshot, text)
        return plan if plan is not None and plan.complete and plan.calls else None

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
            alternative = option_state(option, policy)
            result = evaluate(employee, current, alternative, snap.calc, occupation)
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
            self._request(ctx, MissingField.OCCUPATION_OPT_OUT, Mode.REQUIRED, AnswerType.BOOLEAN,
                          "האם עובד בעיסוק הזה רשאי לוותר על רכב?",
                          {"group": state.profile.group, "occupation_text": state.profile.occupation_text})
            if state.pending_rep is None:
                state.pending_rep = PendingRep(reason=RepReason.OCCUPATION_OPT_OUT, resume_stage=Stage.PRESENTED)

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
