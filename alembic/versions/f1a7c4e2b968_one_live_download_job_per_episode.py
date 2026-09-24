"""one live download job per episode

Revision ID: f1a7c4e2b968
Revises: e5f9a3c1d270
Create Date: 2026-09-24 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f1a7c4e2b968'
down_revision: Union[str, None] = 'e5f9a3c1d270'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Duplicates that slipped in before the index: keep the oldest live job per episode.
    op.execute(
        """
        DELETE FROM download_jobs d USING download_jobs keep
        WHERE d.episode_id = keep.episode_id
          AND d.state IN ('queued', 'running') AND keep.state IN ('queued', 'running')
          AND d.id > keep.id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_download_jobs_live_episode "
        "ON download_jobs (episode_id) WHERE state IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_download_jobs_live_episode")
