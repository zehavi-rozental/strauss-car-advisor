"""Encrypted employee email, one-time return links, and idempotent mail outbox."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.shared.db

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("additional_session_token_hashes", JSON, nullable=False,
                                  server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("email_ciphertext", sa.Text(), nullable=True))
        batch.add_column(sa.Column("email_verified_at", app.shared.db.UtcDateTime(timezone=True), nullable=True))

    op.create_table(
        "conversation_email_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", app.shared.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", app.shared.db.UtcDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.shared.db.UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_email_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_conversation_email_links_token_hash")),
    )
    op.create_index(op.f("ix_conversation_email_links_conversation_id"), "conversation_email_links",
                    ["conversation_id"], unique=False)

    op.create_table(
        "conversation_email_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("recipient_ciphertext", sa.Text(), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", app.shared.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=80), nullable=True),
        sa.Column("created_at", app.shared.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("sent_at", app.shared.db.UtcDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_email_outbox")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_conversation_email_outbox_dedupe_key")),
    )
    op.create_index(op.f("ix_conversation_email_outbox_conversation_id"), "conversation_email_outbox",
                    ["conversation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_email_outbox_conversation_id"), table_name="conversation_email_outbox")
    op.drop_table("conversation_email_outbox")
    op.drop_index(op.f("ix_conversation_email_links_conversation_id"), table_name="conversation_email_links")
    op.drop_table("conversation_email_links")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("email_verified_at")
        batch.drop_column("email_ciphertext")
        batch.drop_column("additional_session_token_hashes")
