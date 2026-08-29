"""take already-played episodes out of every queue

Removing a finished episode from the queue happens on the transition into played, which
does nothing for the ones finished before that existed: they will never transition again,
so they would sit in the queue permanently. That is most of what anyone actually has in
there, and from the outside it looks exactly like the feature not working.

Positions are renumbered afterwards, because the reorder and insert-at-position endpoints
both assume a dense 0..n-1 sequence per user.

Revision ID: f4b8c21e6a07
Revises: e3f1a75c9d44
Create Date: 2026-08-29 13:41:22.870415
"""
from alembic import op

revision = 'f4b8c21e6a07'
down_revision = 'e3f1a75c9d44'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM queue q
        USING episode_state s
        WHERE s.episode_id = q.episode_id
          AND s.user_id = q.user_id
          AND s.played
        """
    )
    # Renumber what is left, per user, preserving the order they were in.
    op.execute(
        """
        UPDATE queue q
        SET position = ranked.rank - 1
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY user_id ORDER BY position, id
                   ) AS rank
            FROM queue
        ) AS ranked
        WHERE ranked.id = q.id
          AND q.position <> ranked.rank - 1
        """
    )


def downgrade() -> None:
    # Nothing to restore: which episodes were in a queue before this ran is not recorded
    # anywhere, and re-adding every played episode would be worse than the gap.
    pass
