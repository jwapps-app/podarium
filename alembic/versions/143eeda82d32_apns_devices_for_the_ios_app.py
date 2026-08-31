"""apns devices for the ios app

The iOS client registers an APNs token here. Kept apart from push_subscriptions, which is
a browser: that is an endpoint URL and a keypair agreed with a push service, this is a
token and the bundle it was issued for, delivered through the shared relay rather than to
Apple from this server.

Revision ID: 143eeda82d32
Revises: f4b8c21e6a07
Create Date: 2026-08-31 16:31:53.577868
"""
from alembic import op
import sqlalchemy as sa


revision = '143eeda82d32'
down_revision = 'f4b8c21e6a07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('apns_devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_token', sa.Text(), nullable=False),
    sa.Column('bundle_id', sa.Text(), nullable=False),
    sa.Column('sandbox', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_token')
    )
    op.create_index(op.f('ix_apns_devices_user_id'), 'apns_devices', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_apns_devices_user_id'), table_name='apns_devices')
    op.drop_table('apns_devices')
