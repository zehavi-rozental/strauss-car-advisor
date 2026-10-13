"""הממשק הציבורי של מודול השיחה: ConversationService ומה שה-API צריך."""
from __future__ import annotations

import hmac
import secrets
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import TypeAdapter
from sqlalchemy.orm import sessionmaker

from app.modules.calculation.public import (
    Employee,
    OccupationRule,
    ScenarioResult,
    State,
    evaluate,
)
from app.modules.rules.public import RulesStore, load_snapshot
from app.modules.vehicles.public import VehicleRepository

from .agent import Agent, NullRepresentative, RepresentativePort
from .llm.factory import build_llm_chain
from .llm.port import ChatMessage, LLMChain, LLMPort
from .parsing import redact_id_numbers
from .repository import DATA_FILE, EXTENSIONS_FILE, RULES_FILE, ConversationRepository, token_hash
from .stage import ConversationState, OptionResult, RepReason, Stage


class ConversationNotFound(Exception):
    """לא קיימת, או שהעוגייה לא שלה (לא מבדילים — כדי לא לחשוף קיום)."""


class ConversationLimitReached(Exception):
    pass


@dataclass
class TurnResult:
    conversation_id: str
    stage: Stage
    reply: str
    source: str
    results: list[OptionResult] = field(default_factory=list)
    pending_rep: Optional[RepReason] = None


@dataclass
class ConversationView:
    conversation_id: str
    stage: Stage
    state: ConversationState
    messages: list[dict[str, Any]]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ConversationNotFound() from exc


class ConversationService:
    def __init__(self, sessions: sessionmaker, rules: RulesStore, llm: LLMChain,
                 rep: Optional[RepresentativePort] = None, max_messages: int = 80):
        self.sessions = sessions
        self.rules = rules
        self.llm = llm
        self.agent = Agent(llm, rep or NullRepresentative())
        self.max_messages = max_messages

    # ---------- שיחה חדשה ----------

    def start(self) -> tuple[TurnResult, str]:
        """מחזיר את תור הפתיחה ואת טוקן השיחה (נשמר רק כ-hash)."""
        token = secrets.token_urlsafe(32)
        state = ConversationState()
        greeting = self.agent.greeting(state, self.rules.current)
        with self.sessions() as session, session.begin():
            repo = ConversationRepository(session)
            row = repo.create(state.model_dump(mode="json"), state.stage.value, token_hash(token), self.llm.mode)
            repo.add_message(row.id, "assistant", greeting, "template")
            conversation_id = str(row.id)
        return TurnResult(conversation_id=conversation_id, stage=state.stage, reply=greeting,
                          source="template"), token

    # ---------- תור ----------

    def send(self, conversation_id: str, token: str, text: str) -> TurnResult:
        snapshot = self.rules.current
        with self.sessions() as session, session.begin():
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            if row.message_count >= self.max_messages:
                raise ConversationLimitReached()
            state = ConversationState.model_validate(row.state)
            history = [ChatMessage("user" if m.role == "user" else "assistant", m.content)
                       for m in repo.messages(row.id)]
            text = redact_id_numbers(text)

            def record_run(label: str, inputs: dict[str, Any], result: ScenarioResult) -> str:
                run = repo.add_run(row.id, snapshot, label, result.engine_version, result.status.value, inputs,
                                   result.model_dump(mode="json"), result.delta_display)
                return str(run.id)

            outcome = self.agent.run_turn(snapshot, VehicleRepository(session), state, history, text, record_run)
            repo.add_message(row.id, "user", text, "user")
            repo.add_message(row.id, "assistant", outcome.reply, outcome.source, outcome.guard_blocked)
            row.state = state.model_dump(mode="json")
            row.stage = state.stage.value
            row.message_count += 1
            row.llm_mode = self.llm.mode
            return TurnResult(conversation_id=str(row.id), stage=state.stage, reply=outcome.reply,
                              source=outcome.source,
                              results=state.results if outcome.just_calculated else [],
                              pending_rep=state.pending_rep.reason if state.pending_rep else None)

    def view(self, conversation_id: str, token: str) -> ConversationView:
        with self.sessions() as session:
            repo = ConversationRepository(session)
            row = self._load(repo, conversation_id, token)
            state = ConversationState.model_validate(row.state)
            messages = [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                        for m in repo.messages(row.id)]
            return ConversationView(conversation_id=str(row.id), stage=state.stage, state=state,
                                    messages=messages)

    def _load(self, repo: ConversationRepository, conversation_id: str, token: str):
        row = repo.get(_uuid(conversation_id))
        if row is None or not token or not hmac.compare_digest(row.session_token_hash, token_hash(token)):
            raise ConversationNotFound()
        return row


# ---------- שחזור חישוב ----------

_STATE = TypeAdapter(State)


def reproduce_run(sessions: sessionmaker, run_id: str) -> ScenarioResult:
    """מריץ שוב חישוב שמור מול קבצי ההגדרות ששמורים איתו — לא מול מה שבתיקייה היום."""
    with sessions() as session:
        repo = ConversationRepository(session)
        run = repo.get_run(_uuid(run_id))
        if run is None:
            raise ConversationNotFound()
        snap_row = repo.get_snapshot(run.config_snapshot_id)
        assert snap_row is not None
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # bytes ולא write_text: בלי המרת שורות, הקובץ זהה למקור
            (d / RULES_FILE).write_bytes(snap_row.strauss_rules_json.encode("utf-8"))
            (d / EXTENSIONS_FILE).write_bytes(snap_row.extensions_yaml.encode("utf-8"))
            (d / DATA_FILE).write_bytes(snap_row.strauss_data_xlsx)
            snapshot = load_snapshot(d)
        inputs = run.inputs
        occupation = OccupationRule.model_validate(inputs["occupation"]) if inputs["occupation"] else None
        return evaluate(Employee.model_validate(inputs["employee"]), _STATE.validate_python(inputs["current"]),
                        _STATE.validate_python(inputs["alternative"]), snapshot.calc, occupation)


__all__ = [
    "ConversationLimitReached", "ConversationNotFound", "ConversationService", "ConversationState",
    "ConversationView", "LLMChain", "LLMPort", "OptionResult", "RepReason", "RepresentativePort", "Stage",
    "TurnResult", "build_llm_chain", "reproduce_run",
]
