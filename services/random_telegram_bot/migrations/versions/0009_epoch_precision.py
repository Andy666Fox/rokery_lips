"""Preserve Unix-second deadlines with double precision on MySQL.

Existing FLOAT rounding cannot be recovered; values and units stay unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Frozen schema at revision 0008, independent of future ORM changes.
EPOCH_COLUMNS = {
    "webhook_inbox": (
        ("received_at", False),
        ("next_attempt_at", False),
        ("lease_until", False),
        ("completed_at", True),
    ),
    "channel_checks": (("next_check_at", False),),
    "verification_control": (("not_before", False),),
    "discovered_channels": (("verified_at", False),),
    "message_deliveries": (("delivered_at", False),),
}
SERVER_DEFAULTS = {("webhook_inbox", "received_at"): sa.text("0")}


def upgrade() -> None:
    for table, columns in EPOCH_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for name, nullable in columns:
                batch.alter_column(
                    name,
                    existing_type=sa.Float(),
                    type_=sa.Double(),
                    existing_nullable=nullable,
                    existing_server_default=SERVER_DEFAULTS.get((table, name)),
                )


def downgrade() -> None:
    # Downgrading deliberately returns to the old, lossy schema.
    for table, columns in reversed(EPOCH_COLUMNS.items()):
        with op.batch_alter_table(table) as batch:
            for name, nullable in columns:
                batch.alter_column(
                    name,
                    existing_type=sa.Double(),
                    type_=sa.Float(),
                    existing_nullable=nullable,
                    existing_server_default=SERVER_DEFAULTS.get((table, name)),
                )
