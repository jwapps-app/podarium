"""apns device failing_since

Revision ID: b2d7f4a91c3e
Revises: 143eeda82d32
Create Date: 2026-09-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d7f4a91c3e'
down_revision: Union[str, None] = '143eeda82d32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'apns_devices',
        sa.Column('failing_since', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('apns_devices', 'failing_since')
