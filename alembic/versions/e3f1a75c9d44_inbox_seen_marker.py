"""when the inbox was last looked at, for the home-screen badge

The badge counts episodes that have arrived since you last looked, so it needs a marker
for "last looked". Deliberately separate from FeedState.last_seen_at, which each show's
own page moves: glancing at the inbox should clear the badge without also clearing the new
marker on every show you did not open.

Existing users are stamped as having just looked. Left NULL they would fall back to the
account's creation date, and the first badge after deploying this would count the entire
back catalogue -- thousands, on a library of any age.

Revision ID: e3f1a75c9d44
Revises: c1d4e7a9b820
Create Date: 2026-08-28 18:41:07.552118
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3f1a75c9d44'
down_revision = 'c1d4e7a9b820'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users', sa.Column('inbox_seen_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE users SET inbox_seen_at = now()")


def downgrade() -> None:
    op.drop_column('users', 'inbox_seen_at')
