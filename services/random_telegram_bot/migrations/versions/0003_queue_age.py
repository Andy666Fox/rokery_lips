"""Detect a queue that accepts events but stops processing them."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_inbox", sa.Column("received_at", sa.Float(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("webhook_inbox", "received_at")
