"""Application-owned relational models for persistent trips and workflows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base, utc_now


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    trips: Mapped[list[Trip]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_expires", "user_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class Trip(TimestampMixin, Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint(
            "status IN ('approved', 'active', 'paused', 'completed', 'cancelled')",
            name="valid_status",
        ),
        Index("ix_trips_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    planning_thread_id: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="approved")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    guardian_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="trips")
    versions: Mapped[list[TripVersion]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    messages: Mapped[list[TripMessage]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    bookings: Mapped[list[Booking]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    monitor_snapshots: Mapped[list[MonitorSnapshot]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    risks: Mapped[list[RiskEvent]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    recovery_proposals: Mapped[list[RecoveryProposal]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )


class TripVersion(Base):
    __tablename__ = "trip_versions"
    __table_args__ = (
        UniqueConstraint(
            "trip_id", "version_number", name="uq_trip_versions_trip_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    structured_plan: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict
    )
    rendered_itinerary: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    trip: Mapped[Trip] = relationship(back_populates="versions")


class TripMessage(Base):
    __tablename__ = "trip_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="valid_role"),
        Index("ix_trip_messages_trip_created", "trip_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False, default="chat")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    trip: Mapped[Trip] = relationship(back_populates="messages")


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("booking_type IN ('flight', 'hotel')", name="valid_type"),
        CheckConstraint(
            "status IN ('searched', 'selected', 'held', 'payment_pending', "
            "'demo_confirmed', 'failed', 'cancelled', 'expired')",
            name="valid_status",
        ),
        Index("ix_bookings_trip_status", "trip_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    booking_type: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    trip: Mapped[Trip] = relationship(back_populates="bookings")
    payment_orders: Mapped[list[PaymentOrder]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )


class PaymentOrder(TimestampMixin, Base):
    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'pending', 'succeeded', 'failed', 'cancelled', 'refunded')",
            name="valid_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_order_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    booking: Mapped[Booking] = relationship(back_populates="payment_orders")
    events: Mapped[list[PaymentEvent]] = relationship(
        back_populates="payment_order", cascade="all, delete-orphan"
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'failed', 'cancelled')", name="valid_outcome"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    payment_order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_event_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    payment_order: Mapped[PaymentOrder] = relationship(back_populates="events")


class MonitorSnapshot(Base):
    __tablename__ = "monitor_snapshots"
    __table_args__ = (Index("ix_monitor_snapshots_trip_checked", "trip_id", "checked_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    flight_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict
    )
    weather_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict
    )
    schedule_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict
    )
    source_status: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict
    )

    trip: Mapped[Trip] = relationship(back_populates="monitor_snapshots")


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        UniqueConstraint(
            "trip_id", "fingerprint", name="uq_risk_events_trip_fingerprint"
        ),
        CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name="valid_severity"),
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="valid_status"),
        Index("ix_risk_events_trip_status", "trip_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    affected_item_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trip: Mapped[Trip] = relationship(back_populates="risks")
    recovery_proposals: Mapped[list[RecoveryProposal]] = relationship(
        back_populates="risk_event", cascade="all, delete-orphan"
    )


class RecoveryProposal(Base):
    __tablename__ = "recovery_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'dismissed', 'superseded')",
            name="valid_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_event_id: Mapped[str] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_version: Mapped[int] = mapped_column(Integer, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    selected_option: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    approval_thread_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    trip: Mapped[Trip] = relationship(back_populates="recovery_proposals")
    risk_event: Mapped[RiskEvent] = relationship(back_populates="recovery_proposals")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_trip_created", "trip_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    trip: Mapped[Trip] = relationship(back_populates="audit_events")
