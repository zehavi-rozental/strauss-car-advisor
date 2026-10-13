"""מסד הנתונים: מנוע, סשן וטיפוסים ניידים בין SQLite ל-PostgreSQL (סעיף 5)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import JSON, DateTime, MetaData, String, TypeDecorator, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import Numeric

from .settings import get_settings

# JSON ב-SQLite, JSONB ב-PostgreSQL
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class Money(TypeDecorator):
    """כסף: NUMERIC(14,4) ב-PostgreSQL, TEXT ב-SQLite (שם NUMERIC עובר דרך float)."""

    impl = String(32)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(14, 4, asdecimal=True))
        return dialect.type_descriptor(String(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = Decimal(value)
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(str(value))


class UtcDateTime(TypeDecorator):
    """תאריך-שעה תמיד ב-UTC ותמיד עם אזור זמן, בשני המסדים."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime ללא אזור זמן")
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def make_engine(url: str) -> Engine:
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


def session_factory(engine: Engine | None = None) -> sessionmaker:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
