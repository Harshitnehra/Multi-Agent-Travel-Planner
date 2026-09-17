"""Add idempotent demo payment events.

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payment_order_id", sa.String(36), nullable=False),
        sa.Column("provider_event_id", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failed', 'cancelled')",
            name="ck_payment_events_valid_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["payment_order_id"],
            ["payment_orders.id"],
            name="fk_payment_events_payment_order_id_payment_orders",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider_event_id", name="uq_payment_events_provider_event_id"
        ),
    )
    op.create_index(
        "ix_payment_events_payment_order_id", "payment_events", ["payment_order_id"]
    )


def downgrade() -> None:
    op.drop_table("payment_events")
