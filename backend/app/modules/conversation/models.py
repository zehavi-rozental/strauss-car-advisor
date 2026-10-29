"""טבלאות השיחה: שיחות, הודעות, הרצות חישוב וגרסאות הגדרות.

אין כאן מספרי זהות. השכר נשמר רק בתוך state של השיחה (לא בלוג).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, Money, PortableJSON, UtcDateTime, new_id, utcnow


class ConfigSnapshotRow(Base):
    """גרסת הגדרות אחת, עם התוכן המלא של כל קובץ — כדי שאפשר לשחזר כל חישוב."""

    __tablename__ = "config_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    combined_hash: Mapped[str] = mapped_column(String(64), unique=True)
    rules_version: Mapped[str] = mapped_column(String(64))
    extensions_version: Mapped[str] = mapped_column(String(64))
    file_hashes: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    strauss_rules_json: Mapped[str] = mapped_column(Text)
    extensions_yaml: Mapped[str] = mapped_column(Text)
    strauss_data_xlsx: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    stage: Mapped[str] = mapped_column(String(32))
    session_token_hash: Mapped[str] = mapped_column(String(64))   # sha256; הטוקן עצמו לא נשמר
    additional_session_token_hashes: Mapped[list[str]] = mapped_column(
        PortableJSON, default=list, server_default=text("'[]'"))
    state: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    email_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    # נעילה אופטימית: כל תור כותב רק אם הגרסה לא השתנתה מאז שקרא אותה
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_mode: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)              # סדר ההודעות בשיחה
    role: Mapped[str] = mapped_column(String(16))          # user / assistant
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))        # user / llm / template
    rep_question_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    guard_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CalculationRunRow(Base):
    __tablename__ = "calculation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    config_snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("config_snapshots.id"))
    engine_version: Mapped[str] = mapped_column(String(16))
    option_label: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    inputs: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    result: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    delta_display: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class EmailLinkRow(Base):
    __tablename__ = "conversation_email_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    purpose: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class EmailOutboxRow(Base):
    __tablename__ = "conversation_email_outbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    purpose: Mapped[str] = mapped_column(String(16))
    recipient_ciphertext: Mapped[str] = mapped_column(Text)
    token_ciphertext: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_error: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
