"""Durable CSV verification queue with indexed fair scheduling."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_checks",
        sa.Column(
            "channel_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("channels.channel_id"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("next_check_at", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(64)),
        sa.Column("import_sha256", sa.String(64), nullable=False),
        sa.Column("source_urls", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
    )
    op.create_index("ix_channel_checks_due", "channel_checks", ["next_check_at", "channel_id"])
    op.create_table(
        "verification_control",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("not_before", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("verification_control")
    op.drop_table("channel_checks")
