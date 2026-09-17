"""Create application-owned TripMate tables.

Revision ID: 0001
Revises: None
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "trips",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("planning_thread_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("timezone", sa.String(64)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("guardian_enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('approved', 'active', 'paused', 'completed', 'cancelled')",
            name="ck_trips_valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_trips_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("planning_thread_id", name="uq_trips_planning_thread_id"),
    )
    op.create_index("ix_trips_user_status", "trips", ["user_id", "status"])

    op.create_table(
        "trip_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("structured_plan", sa.JSON(), nullable=False),
        sa.Column("rendered_itinerary", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_trip_versions_trip_id_trips", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("trip_id", "version_number", name="uq_trip_versions_trip_version"),
    )
    op.create_index("ix_trip_versions_trip_id", "trip_versions", ["trip_id"])

    op.create_table(
        "trip_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_trip_messages_valid_role",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_trip_messages_trip_id_trips", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_trip_messages_trip_created", "trip_messages", ["trip_id", "created_at"]
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("booking_type", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_reference", sa.String(200)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("demo", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("booking_type IN ('flight', 'hotel')", name="ck_bookings_valid_type"),
        sa.CheckConstraint(
            "status IN ('searched', 'selected', 'held', 'payment_pending', "
            "'demo_confirmed', 'failed', 'cancelled', 'expired')",
            name="ck_bookings_valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_bookings_trip_id_trips", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_bookings_trip_status", "bookings", ["trip_id", "status"])

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("booking_id", sa.String(36), nullable=False),
        sa.Column("provider_order_id", sa.String(200)),
        sa.Column("provider_payment_id", sa.String(200)),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('created', 'pending', 'succeeded', 'failed', 'cancelled', 'refunded')",
            name="ck_payment_orders_valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_payment_orders_trip_id_trips", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"], ["bookings.id"], name="fk_payment_orders_booking_id_bookings", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("provider_order_id", name="uq_payment_orders_provider_order_id"),
        sa.UniqueConstraint("provider_payment_id", name="uq_payment_orders_provider_payment_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_orders_idempotency_key"),
    )
    op.create_index("ix_payment_orders_trip_id", "payment_orders", ["trip_id"])
    op.create_index("ix_payment_orders_booking_id", "payment_orders", ["booking_id"])

    op.create_table(
        "monitor_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flight_data", sa.JSON(), nullable=False),
        sa.Column("weather_data", sa.JSON(), nullable=False),
        sa.Column("schedule_data", sa.JSON(), nullable=False),
        sa.Column("source_status", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_monitor_snapshots_trip_id_trips", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_monitor_snapshots_trip_checked", "monitor_snapshots", ["trip_id", "checked_at"]
    )

    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("affected_item_id", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_events_valid_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name="ck_risk_events_valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_risk_events_trip_id_trips", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("trip_id", "fingerprint", name="uq_risk_events_trip_fingerprint"),
    )
    op.create_index("ix_risk_events_trip_status", "risk_events", ["trip_id", "status"])

    op.create_table(
        "recovery_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("risk_event_id", sa.String(36), nullable=False),
        sa.Column("trip_version", sa.Integer(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("selected_option", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("approval_thread_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'dismissed', 'superseded')",
            name="ck_recovery_proposals_valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_recovery_proposals_trip_id_trips", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["risk_event_id"], ["risk_events.id"], name="fk_recovery_proposals_risk_event_id_risk_events", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_recovery_proposals_trip_id", "recovery_proposals", ["trip_id"])
    op.create_index(
        "ix_recovery_proposals_risk_event_id", "recovery_proposals", ["risk_event_id"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.String(36), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("before_data", sa.JSON()),
        sa.Column("after_data", sa.JSON()),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_audit_events_trip_id_trips", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_audit_events_trip_created", "audit_events", ["trip_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("recovery_proposals")
    op.drop_table("risk_events")
    op.drop_table("monitor_snapshots")
    op.drop_table("payment_orders")
    op.drop_table("bookings")
    op.drop_table("trip_messages")
    op.drop_table("trip_versions")
    op.drop_table("trips")
    op.drop_table("users")
