"""שמירה וטעינה: שיחות, הודעות, הרצות חישוב וגרסאות הגדרות."""
from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.rules.public import RulesSnapshot

from .models import CalculationRunRow, ConfigSnapshotRow, ConversationRow, MessageRow

RULES_FILE = "strauss-rules.json"
EXTENSIONS_FILE = "extensions.yaml"
DATA_FILE = "strauss-data.xlsx"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def combined_hash(snapshot: RulesSnapshot) -> str:
    h = hashlib.sha256()
    for name in sorted(snapshot.file_contents):
        h.update(name.encode("utf-8") + b"\0" + snapshot.file_contents[name] + b"\0")
    return h.hexdigest()


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    # ---------- שיחות ----------

    def create(self, state: dict[str, Any], stage: str, token_hash_: str, llm_mode: str) -> ConversationRow:
        row = ConversationRow(stage=stage, state=state, session_token_hash=token_hash_,
                              message_count=0, llm_mode=llm_mode)
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, conversation_id: uuid.UUID) -> Optional[ConversationRow]:
        return self.session.get(ConversationRow, conversation_id)

    # ---------- הודעות ----------

    def add_message(self, conversation_id: uuid.UUID, role: str, content: str, source: str,
                    guard_blocked: bool = False) -> MessageRow:
        seq = self.session.scalar(select(func.coalesce(func.max(MessageRow.seq), 0))
                                  .where(MessageRow.conversation_id == conversation_id)) + 1
        row = MessageRow(conversation_id=conversation_id, seq=seq, role=role, content=content, source=source,
                         guard_blocked=guard_blocked)
        self.session.add(row)
        self.session.flush()
        return row

    def messages(self, conversation_id: uuid.UUID) -> list[MessageRow]:
        return list(self.session.scalars(select(MessageRow).where(MessageRow.conversation_id == conversation_id)
                                         .order_by(MessageRow.seq)))

    # ---------- גרסאות הגדרות והרצות ----------

    def snapshot_row(self, snapshot: RulesSnapshot) -> ConfigSnapshotRow:
        """שורה אחת לכל גרסה, עם התוכן המלא של שלושת הקבצים."""
        key = combined_hash(snapshot)
        row = self.session.scalar(select(ConfigSnapshotRow).where(ConfigSnapshotRow.combined_hash == key))
        if row is None:
            files = snapshot.file_contents
            row = ConfigSnapshotRow(
                combined_hash=key, rules_version=snapshot.calc.rules_version,
                extensions_version=snapshot.calc.extensions_version, file_hashes=dict(snapshot.file_hashes),
                strauss_rules_json=files[RULES_FILE].decode("utf-8"),
                extensions_yaml=files[EXTENSIONS_FILE].decode("utf-8"),
                strauss_data_xlsx=files[DATA_FILE])
            self.session.add(row)
            self.session.flush()
        return row

    def add_run(self, conversation_id: uuid.UUID, snapshot: RulesSnapshot, label: str, engine_version: str,
                status: str, inputs: dict[str, Any], result: dict[str, Any],
                delta: Optional[Decimal]) -> CalculationRunRow:
        row = CalculationRunRow(conversation_id=conversation_id, config_snapshot_id=self.snapshot_row(snapshot).id,
                                engine_version=engine_version, option_label=label[:128], status=status,
                                inputs=inputs, result=result, delta_display=delta)
        self.session.add(row)
        self.session.flush()
        return row

    def get_run(self, run_id: uuid.UUID) -> Optional[CalculationRunRow]:
        return self.session.get(CalculationRunRow, run_id)

    def get_snapshot(self, snapshot_id: uuid.UUID) -> Optional[ConfigSnapshotRow]:
        return self.session.get(ConfigSnapshotRow, snapshot_id)
