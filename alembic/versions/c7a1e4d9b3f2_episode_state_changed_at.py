"""episode_state changed_at

Revision ID: c7a1e4d9b3f2
Revises: b2d7f4a91c3e
Create Date: 2026-09-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1e4d9b3f2'
down_revision: Union[str, None] = 'b2d7f4a91c3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'episode_state',
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Existing rows were last changed no later than they were last written.
    op.execute("UPDATE episode_state SET changed_at = updated_at")


def downgrade() -> None:
    op.drop_column('episode_state', 'changed_at')
