"""A message remembers which results it reported and carries its card; a conversation remembers its last access.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.shared.db

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("results_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("card", app.shared.db.PortableJSON, nullable=True))
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("last_access_at", app.shared.db.UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("last_access_at")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("card")
        batch.drop_column("results_version")
