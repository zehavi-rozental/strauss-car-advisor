"""initial schema: tax-authority vehicles, conversations, messages, calculation runs, config snapshots

Revision ID: 0001
Revises: 
Create Date: 2026-10-13 21:34:34.504213
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from sqlalchemy.dialects import postgresql

import app.shared.db

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table('config_snapshots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('combined_hash', sa.String(length=64), nullable=False),
    sa.Column('rules_version', sa.String(length=64), nullable=False),
    sa.Column('extensions_version', sa.String(length=64), nullable=False),
    sa.Column('file_hashes', JSON, nullable=False),
    sa.Column('strauss_rules_json', sa.Text(), nullable=False),
    sa.Column('extensions_yaml', sa.Text(), nullable=False),
    sa.Column('strauss_data_xlsx', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_config_snapshots')),
    sa.UniqueConstraint('combined_hash', name=op.f('uq_config_snapshots_combined_hash'))
    )
    op.create_table('conversations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('session_token_hash', sa.String(length=64), nullable=False),
    sa.Column('state', JSON, nullable=False),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('llm_mode', sa.String(length=32), nullable=False),
    sa.Column('created_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_table('vehicles_tax_values',
    sa.Column('tax_year', sa.Integer(), nullable=False),
    sa.Column('reg_year', sa.Integer(), nullable=False),
    sa.Column('vehicle_type', sa.String(length=2), nullable=False),
    sa.Column('manufacturer_code', sa.String(length=5), nullable=False),
    sa.Column('model_code', sa.String(length=5), nullable=False),
    sa.Column('manufacturer_name', sa.String(length=64), nullable=False),
    sa.Column('model_description', sa.String(length=64), nullable=False),
    sa.Column('commercial_name', sa.String(length=64), nullable=False),
    sa.Column('fuel_code', sa.String(length=2), nullable=False),
    sa.Column('drive', sa.String(length=10), nullable=False),
    sa.Column('automatic', sa.Boolean(), nullable=False),
    sa.Column('list_price', app.shared.db.Money(length=32), nullable=True),
    sa.Column('use_value', app.shared.db.Money(length=32), nullable=False),
    sa.Column('effective_date', sa.String(length=10), nullable=False),
    sa.PrimaryKeyConstraint('tax_year', 'reg_year', 'vehicle_type', 'manufacturer_code', 'model_code', name=op.f('pk_vehicles_tax_values'))
    )
    with op.batch_alter_table('vehicles_tax_values', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicles_tax_values_commercial_name'), ['commercial_name'], unique=False)

    op.create_table('calculation_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('config_snapshot_id', sa.Uuid(), nullable=False),
    sa.Column('engine_version', sa.String(length=16), nullable=False),
    sa.Column('option_label', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('inputs', JSON, nullable=False),
    sa.Column('result', JSON, nullable=False),
    sa.Column('delta_display', app.shared.db.Money(length=32), nullable=True),
    sa.Column('created_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['config_snapshot_id'], ['config_snapshots.id'], name=op.f('fk_calculation_runs_config_snapshot_id_config_snapshots')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_calculation_runs_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calculation_runs'))
    )
    with op.batch_alter_table('calculation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_calculation_runs_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('guard_blocked', sa.Boolean(), nullable=False),
    sa.Column('created_at', app.shared.db.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_conversation_id'), ['conversation_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_conversation_id'))

    op.drop_table('messages')
    with op.batch_alter_table('calculation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_calculation_runs_conversation_id'))

    op.drop_table('calculation_runs')
    with op.batch_alter_table('vehicles_tax_values', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicles_tax_values_commercial_name'))

    op.drop_table('vehicles_tax_values')
    op.drop_table('conversations')
    op.drop_table('config_snapshots')
