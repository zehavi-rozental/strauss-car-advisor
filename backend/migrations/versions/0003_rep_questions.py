"""rep_questions: questions to the transport representative (owned by the cases module)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14 22:32:44.353709
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import postgresql

import app.shared.db

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table('rep_questions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('missing_field', sa.String(length=32), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('option_id', sa.String(length=32), nullable=False),
    sa.Column('context', JSON, nullable=False),
    sa.Column('question_text', sa.Text(), nullable=False),
    sa.Column('answer_type', sa.String(length=16), nullable=False),
    sa.Column('suggested_value', sa.String(length=64), nullable=True),
    sa.Column('answer_value', sa.String(length=500), nullable=True),
    sa.Column('answer_note', sa.Text(), nullable=True),
    sa.Column('effective_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.Column('answered_at', app.shared.db.UtcDateTime(timezone=True), nullable=True),
    sa.Column('answered_by', sa.String(length=60), nullable=True),
    sa.Column('version', sa.Integer(), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rep_questions'))
    )
    with op.batch_alter_table('rep_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rep_questions_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index('uq_rep_questions_open', ['conversation_id', 'missing_field', 'option_id'], unique=True,
                              sqlite_where=sa.text("status = 'OPEN'"), postgresql_where=sa.text("status = 'OPEN'"))


def downgrade() -> None:
    with op.batch_alter_table('rep_questions', schema=None) as batch_op:
        batch_op.drop_index('uq_rep_questions_open', sqlite_where=sa.text("status = 'OPEN'"), postgresql_where=sa.text("status = 'OPEN'"))
        batch_op.drop_index(batch_op.f('ix_rep_questions_conversation_id'))

    op.drop_table('rep_questions')
