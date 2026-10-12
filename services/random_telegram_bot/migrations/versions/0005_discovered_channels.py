"""Track external catalogue entries by immutable Telegram identity and provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovered_channels",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("channels.channel_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.Float(), nullable=False),
        sa.Column("identity_source", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discovered_channels")
