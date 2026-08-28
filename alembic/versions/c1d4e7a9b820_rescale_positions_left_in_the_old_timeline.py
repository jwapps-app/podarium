"""move saved positions that were left in the untrimmed timeline

Trimming makes an episode shorter -- better than a tenth of a talk show -- so a position
recorded against the original file points somewhere later in the trimmed one. Processing
rescales the saved positions when it finishes, but only when it has both durations to
divide, and for a while it could finish without recording them: an exception between
writing the processed path and measuring the file left the row half written, and the
durations were filled in later by the startup reconcile, which does not touch positions.
Those episodes kept a position in a timeline that no longer exists.

Which rows those are is decidable. The rescale updates the state row, so any row it
touched has an updated_at at or after the episode's processed_at. A row still sitting on
an earlier updated_at was never rescaled. A position past the end of the trimmed file is
proof on its own, whatever the timestamps say.

updated_at is set explicitly, both so this cannot apply twice and because delta sync reads
it: a position corrected here has to reach the clients holding the old one.

Revision ID: c1d4e7a9b820
Revises: 4fb73cf43a54
Create Date: 2026-08-28 15:02:44.310901
"""
from alembic import op

revision = 'c1d4e7a9b820'
down_revision = '4fb73cf43a54'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE episode_state AS s
        SET position_seconds = FLOOR(
                s.position_seconds
                * (e.processed_duration_seconds / e.source_duration_seconds)
            )::int,
            updated_at = now()
        FROM episodes AS e
        WHERE s.episode_id = e.id
          AND e.processed_path IS NOT NULL
          AND e.processed_at IS NOT NULL
          AND e.source_duration_seconds > 0
          AND e.processed_duration_seconds > 0
          -- Only shrinking. Levelling alone leaves the length alone, and a ratio at or
          -- above one would move a position forward, which nothing here should ever do.
          AND e.processed_duration_seconds < e.source_duration_seconds
          AND s.position_seconds > 0
          AND (
              -- Never rescaled: the rescale would have stamped the row.
              s.updated_at < e.processed_at
              -- Or self-evidently in the old timeline, whatever the timestamps say.
              OR s.position_seconds > e.processed_duration_seconds
          )
        """
    )


def downgrade() -> None:
    # Deliberately not reversible. The inverse would multiply every position back up,
    # including the ones that were always correct, which is the corruption this repairs.
    pass
