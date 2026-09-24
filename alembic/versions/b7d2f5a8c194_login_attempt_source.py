"""login_attempts.source

Revision ID: b7d2f5a8c194
Revises: a3c6e9f2d581
Create Date: 2026-09-25 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2f5a8c194'
down_revision: Union[str, None] = 'a3c6e9f2d581'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('login_attempts', sa.Column('source', sa.String(length=64), nullable=True))
    op.create_index('ix_login_attempts_source', 'login_attempts', ['source'])


def downgrade() -> None:
    op.drop_index('ix_login_attempts_source', table_name='login_attempts')
    op.drop_column('login_attempts', 'source')
