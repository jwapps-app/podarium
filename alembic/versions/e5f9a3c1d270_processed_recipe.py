"""processed_recipe

Revision ID: e5f9a3c1d270
Revises: d4e8b2c7a915
Create Date: 2026-09-24 12:00:00.000000

Left NULL for files processed before this existed. The reconciler reads NULL as "not
the current recipe" and rebuilds them, which is wanted: they were made with the silence
filter that cut every quarter-second pause.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f9a3c1d270'
down_revision: Union[str, None] = 'd4e8b2c7a915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('episodes', sa.Column('processed_recipe', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('episodes', 'processed_recipe')
