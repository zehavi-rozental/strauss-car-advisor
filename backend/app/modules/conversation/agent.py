"""תור אחד בשיחה: חילוץ → כלים → קידום המכונה → חישוב → ניסוח → שומר מספרים.

הקוד קובע את השלב ומחשב; מודל השפה (אם יש) רק מחלץ ומנסח.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, Sequence

from app.modules.calculation.public import ScenarioResult, evaluate
from app.modules.rules.public import RulesSnapshot
from app.modules.vehicles.public import VehicleRepository

from .fallback import fallback_calls
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
from .tools import TOOL_SPECS, ToolContext, available_options, engine_inputs, option_state, run_tool

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 8

EXTRACT_PROMPT = """You are the data-extraction step of a Hebrew-speaking company-car advisor at Strauss.
You never calculate and never invent numbers: you only call tools to record what the employee said.
Stage: {stage}. Still missing: {missing}.
{context}
Rules:
- Pass only values the employee stated. Omit anything not stated.
- Percentages as plain numbers (6 for 6%). Money in shekels.
- "none" / "אין" for commissions, bonus or study fund means 0.
- If the employee does not know their current copay or use value, set the matching *_unknown flag.
- Car models in Latin letters as on the tax-authority list (e.g. "קורולה קרוס" -> "COROLLA CROSS").
- When there is nothing to record, reply with an empty message and no tool call."""

PHRASE_PROMPT = """Rewrite the draft below as one friendly reply in Hebrew to the employee.
Keep every number, amount and list item exactly as in the draft.
Do not add numbers, estimates, calculations or advice that are not in the draft.
Return only the reply text."""


class RepresentativePort(Protocol):
    """שאלה חוסמת לנציג התחבורה. ההקשר: קבוצה, רכב וסכום מוצע בלבד."""

    def open_question(self, reason: RepReason, context: dict[str, Optional[str]]) -> str:
        ...


class NullRepresentative:
    """עד שמודול cases ייבנה (שלב 3): רק מחזיר מזהה."""

    def open_question(self, reason: RepReason, context: dict[str, Optional[str]]) -> str:
        from app.shared.db import new_id
        log.info("rep question opened reason=%s", reason.value)
        return str(new_id())


RecordRun = Callable[[str, dict[str, Any], ScenarioResult], str]


@dataclass
class TurnOutcome:
    reply: str
    source: str               # llm / template
    guard_blocked: bool = False
    just_calculated: bool = False


class Agent:
    def __init__(self, llm: LLMChain, rep: RepresentativePort):
        self.llm = llm
        self.rep = rep

    def greeting(self, state: ConversationState, snapshot: RulesSnapshot) -> str:
        return GREETING + question("occupation", state, snapshot)

    def run_turn(self, snapshot: RulesSnapshot, vehicles: VehicleRepository, state: ConversationState,
                 history: Sequence[ChatMessage], text: str, record_run: RecordRun) -> TurnOutcome:
        text = redact_id_numbers(text)
        ctx = ToolContext(snapshot=snapshot, vehicles=vehicles, state=state)
        before = next_stage(state)

        if before == Stage.WAITING_FOR_REP:
            return TurnOutcome(reply=render(state, snapshot, understood=True, just_calculated=False),
                               source="template")

        used_llm = False
        understood = False
        if self.llm.available:
            try:
                understood = self._extract_llm(ctx, history, text)
                used_llm = True
            except LLMError:
                log.warning("llm extraction failed; falling back to templates")
        if not used_llm:
            understood = self._execute(ctx, fallback_calls(state, snapshot, text))

        self._open_blocking_rep(ctx)
        just_calculated = False
        if next_stage(state) == Stage.CALCULATING:
            self._calculate(ctx, record_run)
            just_calculated = True
        state.stage = next_stage(state)
        understood = understood or state.stage != before

        draft = render(state, snapshot, understood=understood, just_calculated=just_calculated)
        if not used_llm:
            return TurnOutcome(reply=draft, source="template", just_calculated=just_calculated)
        reply, source, blocked = self._phrase(state, draft, text)
        return TurnOutcome(reply=reply, source=source, guard_blocked=blocked, just_calculated=just_calculated)

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

    def _extract_llm(self, ctx: ToolContext, history: Sequence[ChatMessage], text: str) -> bool:
        messages: list[ChatMessage] = [self._system(ctx), *history[-HISTORY_MESSAGES:], ChatMessage("user", text)]
        understood = False
        for _ in range(MAX_TOOL_ROUNDS):
            names = ALLOWED_TOOLS[next_stage(ctx.state)]
            if not names:
                break
            messages[0] = self._system(ctx)
            response = self.llm.complete(messages, [TOOL_SPECS[n] for n in names])
            if not response.tool_calls:
                break
            messages.append(ChatMessage("assistant", response.text, tool_calls=response.tool_calls))
            for call in response.tool_calls:
                facts = self._run(ctx, call)
                understood = understood or facts["ok"]
                messages.append(ChatMessage("tool", json.dumps(facts, ensure_ascii=False, default=str),
                                            tool_call_id=call.id, name=call.name))
        return understood

    def _run(self, ctx: ToolContext, call: ToolCall) -> dict[str, Any]:
        """רק הכלים שהשלב הנוכחי מתיר. כלי אחר מתועד ונדחה."""
        if call.name not in ALLOWED_TOOLS[next_stage(ctx.state)]:
            log.info("tool %s rejected at stage %s", call.name, next_stage(ctx.state).value)
            return {"ok": False, "error": "tool not allowed at this stage"}
        return run_tool(ctx, call.name, call.args)

    def _execute(self, ctx: ToolContext, calls: Sequence[ToolCall]) -> bool:
        return any([self._run(ctx, call)["ok"] for call in calls])

    # ---------- נציג ----------

    def _open_rep(self, ctx: ToolContext, reason: RepReason, suggested: Optional[str], resume: Stage) -> None:
        p, c = ctx.state.profile, ctx.state.current
        context = {"group": p.group, "car": c.label, "suggested_amount": suggested}
        qid = self.rep.open_question(reason, context)
        ctx.state.pending_rep = PendingRep(reason=reason, question_id=qid, resume_stage=resume)

    def _open_blocking_rep(self, ctx: ToolContext) -> None:
        if ctx.state.pending_rep is not None:
            return
        reason = blocking_rep_reason(ctx.state)
        if reason is None:
            return
        suggested = None
        if reason == RepReason.CURRENT_COPAY and ctx.state.current.catalog_code:
            car = next(x for x in ctx.snapshot.catalog if x.catalog_code == ctx.state.current.catalog_code)
            suggested = str(car.default_copay)
        self._open_rep(ctx, reason, suggested, Stage.COLLECT_CURRENT_CAR)

    # ---------- חישוב ----------

    def _calculate(self, ctx: ToolContext, record_run: RecordRun) -> None:
        state, snap = ctx.state, ctx.snapshot
        employee, current, occupation = engine_inputs(snap, state)
        options = {o.id: o for o in available_options(snap, state)}
        results: list[OptionResult] = []
        needs_rep = False
        for option_id in state.selected:
            option = options.get(option_id)
            if option is None:
                continue
            alternative = option_state(option)
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
            needs_rep = needs_rep or "OCCUPATION_OPT_OUT" in result.missing_fields
        state.results = results
        state.calculated_for = list(state.selected)
        if needs_rep and state.pending_rep is None:
            self._open_rep(ctx, RepReason.OCCUPATION_OPT_OUT, None, Stage.PRESENTED)

    # ---------- ניסוח ----------

    def _phrase(self, state: ConversationState, draft: str, text: str) -> tuple[str, str, bool]:
        allowed = collect_numbers(state.model_dump(mode="json")) | collect_numbers(draft)
        try:
            response = self.llm.complete([ChatMessage("system", PHRASE_PROMPT),
                                          ChatMessage("user", f"Draft:\n{draft}\n\nEmployee's message:\n{text}")])
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
