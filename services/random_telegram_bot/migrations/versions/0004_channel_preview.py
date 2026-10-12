"""Persist public channel audience and publication observations without changing existing status."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("subscriber_count", sa.BigInteger(), nullable=True))
    op.add_column("channels", sa.Column("subscriber_display", sa.String(32), nullable=True))
    op.add_column("channels", sa.Column("last_post_at", sa.DateTime(), nullable=True))
    op.add_column("channels", sa.Column("last_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for name in ("last_checked_at", "last_post_at", "subscriber_display", "subscriber_count"):
        op.drop_column("channels", name)
