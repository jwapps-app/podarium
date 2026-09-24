"""one timeline: positions stored on the original's clock

Revision ID: a3c6e9f2d581
Revises: f1a7c4e2b968
Create Date: 2026-09-25 09:00:00.000000

Until now a position for an episode with a trimmed copy was stored on the trimmed copy's
clock (c1d4e7a9b820 moved them there). From here every stored second is on the
original's clock and translated at the API. This moves existing positions back, by the
same proportion that moved them, so what a client is handed does not change.

Bookmarks are left alone: which clock each was made on was never recorded, and most were
made before trimming existed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c6e9f2d581'
down_revision: Union[str, None] = 'f1a7c4e2b968'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('trim_map_json', sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE episode_state AS s
        SET position_seconds = FLOOR(
                s.position_seconds
                * (e.source_duration_seconds / e.processed_duration_seconds)
            )::int
        FROM episodes AS e
        WHERE s.episode_id = e.id
          AND e.processed_path IS NOT NULL
          AND e.source_duration_seconds > 0
          AND e.processed_duration_seconds > 0
          AND e.processed_duration_seconds < e.source_duration_seconds
          AND s.position_seconds > 0
        """
    )


def downgrade() -> None:
    op.drop_column('episodes', 'trim_map_json')
