"""שאלות לנציג התחבורה. הטבלה שייכת למודול cases בלבד.

conversation_id הוא מזהה בלבד (לא מפתח זר): המודול יכול לעבור לשירות נפרד.
ההקשר (context) מכיל רק מה שהנציג צריך — אף פעם לא שכר, סכומי פרופיל, ת"ז או תוכן הודעות.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Date, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, PortableJSON, UtcDateTime, new_id, utcnow


class RepQuestionRow(Base):
    __tablename__ = "rep_questions"
    __table_args__ = (
        # לכל היותר שאלה פתוחה אחת לאותה שיחה + שדה + חלופה — גם כשיש תורות במקביל
        Index("uq_rep_questions_open", "conversation_id", "missing_field", "option_id", unique=True,
              sqlite_where=text("status = 'OPEN'"), postgresql_where=text("status = 'OPEN'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    missing_field: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))                 # CONFIRM / REQUIRED
    option_id: Mapped[str] = mapped_column(String(32), default="")  # "" כשלא רלוונטי
    context: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    question_text: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str] = mapped_column(String(16))          # MONEY / BOOLEAN / DATE / TEXT
    suggested_value: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    answer_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    answer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16))               # OPEN / ANSWERED / CANCELLED
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    answered_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)
    answered_by: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
