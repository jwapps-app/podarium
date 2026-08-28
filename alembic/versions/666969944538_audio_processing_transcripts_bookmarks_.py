"""audio processing, transcripts, bookmarks, integrity

Revision ID: 666969944538
Revises: da26ba6181a6
Create Date: 2026-08-27 22:32:28.322447
"""
from alembic import op
import sqlalchemy as sa


revision = '666969944538'
down_revision = 'da26ba6181a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('bookmarks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('episode_id', sa.Integer(), nullable=False),
    sa.Column('position_seconds', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bookmarks_episode_id'), 'bookmarks', ['episode_id'], unique=False)
    op.create_index(op.f('ix_bookmarks_updated_at'), 'bookmarks', ['updated_at'], unique=False)
    op.create_index(op.f('ix_bookmarks_user_id'), 'bookmarks', ['user_id'], unique=False)
    op.add_column('episodes', sa.Column('transcript_url', sa.Text(), nullable=True))
    op.add_column('episodes', sa.Column('transcript_type', sa.String(length=128), nullable=True))
    op.add_column('episodes', sa.Column('transcript_text', sa.Text(), nullable=True))
    op.add_column('episodes', sa.Column('transcript_fetched_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('episodes', sa.Column('processed_path', sa.Text(), nullable=True))
    op.add_column('episodes', sa.Column('processed_bytes', sa.BigInteger(), nullable=True))
    op.add_column('episodes', sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('episodes', sa.Column('audio_sha256', sa.String(length=64), nullable=True))
    op.add_column('episodes', sa.Column('replaced_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('feeds', sa.Column('trim_silence', sa.Boolean(), nullable=True))
    op.add_column('feeds', sa.Column('normalize_audio', sa.Boolean(), nullable=True))
    op.add_column('feeds', sa.Column('skip_sponsor_chapters', sa.Boolean(), nullable=True))
    op.add_column('feeds', sa.Column('intro_skip_seconds', sa.Integer(), server_default='0', nullable=False))
    op.add_column('feeds', sa.Column('outro_skip_seconds', sa.Integer(), server_default='0', nullable=False))
    op.add_column('settings', sa.Column('global_trim_silence', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('settings', sa.Column('global_normalize_audio', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('settings', sa.Column('global_skip_sponsor_chapters', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    # Searching a library by what was said means matching against whole transcripts, and a
    # LIKE over megabytes of text per episode would scan every one of them. A GIN index over
    # the parsed document turns that into a lookup. Built on the expression rather than a
    # stored column so there is nothing to keep in sync on write.
    op.execute(
        "CREATE INDEX ix_episodes_transcript_fts ON episodes "
        "USING gin (to_tsvector('english', coalesce(transcript_text, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_episodes_transcript_fts")
    op.drop_column('settings', 'global_skip_sponsor_chapters')
    op.drop_column('settings', 'global_normalize_audio')
    op.drop_column('settings', 'global_trim_silence')
    op.drop_column('feeds', 'outro_skip_seconds')
    op.drop_column('feeds', 'intro_skip_seconds')
    op.drop_column('feeds', 'skip_sponsor_chapters')
    op.drop_column('feeds', 'normalize_audio')
    op.drop_column('feeds', 'trim_silence')
    op.drop_column('episodes', 'replaced_at')
    op.drop_column('episodes', 'audio_sha256')
    op.drop_column('episodes', 'processed_at')
    op.drop_column('episodes', 'processed_bytes')
    op.drop_column('episodes', 'processed_path')
    op.drop_column('episodes', 'transcript_fetched_at')
    op.drop_column('episodes', 'transcript_text')
    op.drop_column('episodes', 'transcript_type')
    op.drop_column('episodes', 'transcript_url')
    op.drop_index(op.f('ix_bookmarks_user_id'), table_name='bookmarks')
    op.drop_index(op.f('ix_bookmarks_updated_at'), table_name='bookmarks')
    op.drop_index(op.f('ix_bookmarks_episode_id'), table_name='bookmarks')
    op.drop_table('bookmarks')
