"""per show speed, chapters, push subscriptions

Revision ID: 2b6f5c00ad7a
Revises: af7065f94291
Create Date: 2026-08-26 19:00:02.629316
"""
from alembic import op
import sqlalchemy as sa


revision = '2b6f5c00ad7a'
down_revision = 'af7065f94291'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.Text(), nullable=False),
    sa.Column('auth', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('endpoint')
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.add_column('episodes', sa.Column('chapters_url', sa.Text(), nullable=True))
    op.add_column('episodes', sa.Column('chapters_json', sa.Text(), nullable=True))
    op.add_column('episodes', sa.Column('chapters_fetched_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('feeds', sa.Column('playback_rate', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('feeds', 'playback_rate')
    op.drop_column('episodes', 'chapters_fetched_at')
    op.drop_column('episodes', 'chapters_json')
    op.drop_column('episodes', 'chapters_url')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
