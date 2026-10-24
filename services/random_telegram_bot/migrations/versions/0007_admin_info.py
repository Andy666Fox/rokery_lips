"""First confirmations and successful message delivery timestamps for /info."""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_channels_confirmed_at", "channels", ["confirmed_at"])
    op.create_index("ix_inbox_received_at", "webhook_inbox", ["received_at"])
    op.create_table(
        "message_deliveries",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("delivered_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_message_deliveries_delivered_at", "message_deliveries", ["delivered_at"])
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE channels SET confirmed_at = last_checked_at "
            "WHERE status = TRUE AND last_checked_at IS NOT NULL"
        )
    )
    # The older discovery importer stores its confirmation time in a separate ledger.
    channels = sa.table("channels", sa.column("channel_id"), sa.column("confirmed_at"))
    discovered = connection.execute(
        sa.text(
            "SELECT d.channel_id, d.verified_at FROM discovered_channels d "
            "JOIN channels c ON c.channel_id = d.channel_id WHERE c.confirmed_at IS NULL"
        )
    ).all()
    for identifier, checked in discovered:
        connection.execute(
            channels.update()
            .where(channels.c.channel_id == identifier)
            .values(confirmed_at=datetime.fromtimestamp(checked, UTC).replace(tzinfo=None))
        )
    # Older successful replies have no individual send timestamp. Their completed
    # inbox entry is the available historical evidence; failed entries are excluded.
    deliveries = sa.table("message_deliveries", sa.column("delivered_at"))
    rows = connection.execute(
        sa.text(
            "SELECT completed_at, actions FROM webhook_inbox "
            "WHERE state = 'done' AND completed_at IS NOT NULL"
        )
    ).all()
    batch = []
    for completed, actions in rows:
        if isinstance(actions, str):
            actions = json.loads(actions)
        for action in actions or []:
            if action.get("method") == "sendMessage":
                batch.append({"delivered_at": completed})
            if len(batch) >= 1000:
                connection.execute(deliveries.insert(), batch)
                batch = []
    if batch:
        connection.execute(deliveries.insert(), batch)


def downgrade() -> None:
    op.drop_table("message_deliveries")
    op.drop_index("ix_inbox_received_at", table_name="webhook_inbox")
    op.drop_index("ix_channels_confirmed_at", table_name="channels")
    op.drop_column("channels", "confirmed_at")
