"""per feed notification toggle

Revision ID: da26ba6181a6
Revises: 2b6f5c00ad7a
Create Date: 2026-08-26 20:33:01.146854
"""
from alembic import op
import sqlalchemy as sa


revision = 'da26ba6181a6'
down_revision = '2b6f5c00ad7a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('feeds', sa.Column('notify', sa.Boolean(), server_default=sa.text('true'), nullable=False))


def downgrade() -> None:
    op.drop_column('feeds', 'notify')
