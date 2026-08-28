"""measured listening time

Revision ID: afa3fb403917
Revises: 666969944538
Create Date: 2026-08-28 07:23:54.434913
"""
from alembic import op
import sqlalchemy as sa


revision = 'afa3fb403917'
down_revision = '666969944538'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('episode_state', sa.Column('listened_seconds', sa.Integer(), server_default='0', nullable=False))
    op.drop_index(op.f('ix_episodes_transcript_fts'), table_name='episodes', postgresql_using='gin')


def downgrade() -> None:
    op.create_index(op.f('ix_episodes_transcript_fts'), 'episodes', [sa.literal_column("to_tsvector('english'::regconfig, COALESCE(transcript_text, ''::text))")], unique=False, postgresql_using='gin')
    op.drop_column('episode_state', 'listened_seconds')
