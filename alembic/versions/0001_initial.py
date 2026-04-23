"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_payment_status = PgEnum(
    "initiated", "processed", "failed",
    name="payment_status", create_type=False,
)
_settlement_status = PgEnum(
    "pending", "settled", "not_applicable",
    name="settlement_status", create_type=False,
)
_conflict_reason = PgEnum(
    "stuck_settlement", "settled_after_fail",
    "conflicting_transition", "duplicate_detected",
    name="conflict_reason", create_type=False,
)


def upgrade() -> None:
    op.execute("CREATE TYPE payment_status AS ENUM ('initiated', 'processed', 'failed')")
    op.execute("CREATE TYPE settlement_status AS ENUM ('pending', 'settled', 'not_applicable')")
    op.execute(
        "CREATE TYPE conflict_reason AS ENUM ("
        "'stuck_settlement', 'settled_after_fail', "
        "'conflicting_transition', 'duplicate_detected')"
    )

    op.create_table(
        "merchants",
        sa.Column("merchant_id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.Text, primary_key=True),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.merchant_id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("payment_status", _payment_status, nullable=False),
        sa.Column("settlement_status", _settlement_status, nullable=False),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "has_conflict",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("conflict_reason", _conflict_reason, nullable=True),
    )
    op.create_index(
        "ix_txn_merchant_time",
        "transactions",
        ["merchant_id", sa.text("last_event_at DESC")],
    )
    op.create_index(
        "ix_txn_statuses", "transactions", ["payment_status", "settlement_status"]
    )
    op.create_index(
        "ix_txn_last_event", "transactions", [sa.text("last_event_at DESC")]
    )
    op.create_index("ix_txn_has_conflict", "transactions", ["has_conflict"])

    op.create_table(
        "events",
        sa.Column("event_id", sa.Text, primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Text,
            sa.ForeignKey("transactions.transaction_id"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            sa.Text,
            sa.ForeignKey("merchants.merchant_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.Text, nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("raw_payload", JSONB, nullable=False),
    )
    op.create_index(
        "ix_events_txn_time",
        "events",
        ["transaction_id", sa.text("event_timestamp")],
    )


def downgrade() -> None:
    op.drop_index("ix_events_txn_time", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_txn_has_conflict", table_name="transactions")
    op.drop_index("ix_txn_last_event", table_name="transactions")
    op.drop_index("ix_txn_statuses", table_name="transactions")
    op.drop_index("ix_txn_merchant_time", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("merchants")
    op.execute("DROP TYPE conflict_reason")
    op.execute("DROP TYPE settlement_status")
    op.execute("DROP TYPE payment_status")
