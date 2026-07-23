"""add unified credits pool to subscription

Revision ID: f776e60b33ef
Revises: 14532c41862a
Create Date: 2026-07-23 20:57:42.027236

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f776e60b33ef'
down_revision: Union[str, None] = '14532c41862a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default нужен как и в предыдущей похожей миграции — ADD COLUMN NOT NULL
    # без default падает на таблице с существующими строками (текущие free-пользователи).
    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.add_column(sa.Column('credits_granted', sa.Integer(), nullable=False, server_default='2'))
        batch_op.add_column(sa.Column('credits_used', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.drop_column('credits_used')
        batch_op.drop_column('credits_granted')
