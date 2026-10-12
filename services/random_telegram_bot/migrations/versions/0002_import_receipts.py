"""Record completed legacy imports in the same transaction as imported data."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legacy_imports",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("legacy_imports")
