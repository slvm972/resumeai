"""add lemonsqueezy payment fields

Revision ID: 14532c41862a
Revises: 2d3b2a4a8dd3
Create Date: 2026-07-18 08:58:11.820826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14532c41862a'
down_revision: Union[str, None] = '2d3b2a4a8dd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table используется для совместимости с SQLite (локальная
    # разработка), которая не поддерживает ALTER COLUMN / CREATE UNIQUE INDEX
    # напрямую в рамках одной операции — Alembic в batch-режиме пересоздаёт
    # таблицу. На Postgres (production, Render) выполняется как обычные
    # ALTER TABLE / CREATE INDEX без пересоздания.
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payer_email', sa.String(length=255), nullable=True))
        batch_op.alter_column('user_id',
                   existing_type=sa.INTEGER(),
                   nullable=True)
        batch_op.create_index(batch_op.f('ix_payment_external_id'), ['external_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_external_id'))
        batch_op.alter_column('user_id',
                   existing_type=sa.INTEGER(),
                   nullable=False)
        batch_op.drop_column('payer_email')