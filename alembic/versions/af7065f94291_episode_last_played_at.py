"""episode last played at

Revision ID: af7065f94291
Revises: 30f124d057e3
Create Date: 2026-08-26 18:35:46.648224
"""
from alembic import op
import sqlalchemy as sa


revision = 'af7065f94291'
down_revision = '30f124d057e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('episode_state', sa.Column('last_played_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_episode_state_last_played_at'), 'episode_state', ['last_played_at'], unique=False)

    # Seed from updated_at for anything already part-listened, so episodes you are in the
    # middle of right now appear in "In progress" immediately rather than only after you
    # next touch them. updated_at is the wrong clock in general -- that is why this column
    # exists -- but for a row whose position has moved it is the best estimate available,
    # and it is only ever used until real playback restamps it.
    op.execute(
        """
        UPDATE episode_state
           SET last_played_at = updated_at
         WHERE position_seconds > 0
           AND played = false
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_episode_state_last_played_at'), table_name='episode_state')
    op.drop_column('episode_state', 'last_played_at')
