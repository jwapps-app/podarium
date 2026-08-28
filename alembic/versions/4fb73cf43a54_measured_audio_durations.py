"""measured audio durations

Revision ID: 4fb73cf43a54
Revises: afa3fb403917
Create Date: 2026-08-28 09:07:19.511239
"""
from alembic import op
import sqlalchemy as sa


revision = '4fb73cf43a54'
down_revision = 'afa3fb403917'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('source_duration_seconds', sa.Float(), nullable=True))
    op.add_column('episodes', sa.Column('processed_duration_seconds', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('episodes', 'processed_duration_seconds')
    op.drop_column('episodes', 'source_duration_seconds')
