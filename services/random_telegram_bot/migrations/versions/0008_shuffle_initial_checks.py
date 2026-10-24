"""Persist random ordering of untouched channels without changing retry deadlines."""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_checks",
        sa.Column("queue_order", sa.BigInteger(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    checks = sa.table(
        "channel_checks",
        sa.column("channel_id", sa.BigInteger()),
        sa.column("state", sa.String()),
        sa.column("attempts", sa.Integer()),
        sa.column("queue_order", sa.BigInteger()),
    )
    untouched = sa.and_(checks.c.state == "pending", checks.c.attempts == 0)
    if connection.dialect.name == "mysql":
        # 53-bit integers fit signed BIGINT and the RNG's double precision.
        connection.execute(
            checks.update()
            .where(untouched)
            .values(queue_order=sa.func.floor(sa.func.rand() * 2**53))
        )
    else:
        # SQLite is used by migration tests; production uses the set-based update.
        identifiers = list(connection.scalars(sa.select(checks.c.channel_id).where(untouched)))
        for identifier in identifiers:
            connection.execute(
                checks.update()
                .where(checks.c.channel_id == identifier)
                .values(queue_order=secrets.randbits(53))
            )
    op.drop_index("ix_channel_checks_due", table_name="channel_checks")
    op.create_index(
        "ix_channel_checks_due", "channel_checks", ["next_check_at", "queue_order", "channel_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_channel_checks_due", table_name="channel_checks")
    op.create_index("ix_channel_checks_due", "channel_checks", ["next_check_at", "channel_id"])
    op.drop_column("channel_checks", "queue_order")
