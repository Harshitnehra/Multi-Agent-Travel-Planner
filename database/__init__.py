"""Database connections and models for application-owned TripMate data."""

from database.base import Base
from database.connection import (
    build_engine,
    create_schema,
    get_engine,
    get_session,
    session_scope,
)
from database.models import (
    AuditEvent,
    Booking,
    MonitorSnapshot,
    PaymentOrder,
    PaymentEvent,
    RecoveryProposal,
    RiskEvent,
    Trip,
    TripMessage,
    TripVersion,
    User,
    UserSession,
)

__all__ = [
    "AuditEvent",
    "Base",
    "Booking",
    "MonitorSnapshot",
    "PaymentOrder",
    "PaymentEvent",
    "RecoveryProposal",
    "RiskEvent",
    "Trip",
    "TripMessage",
    "TripVersion",
    "User",
    "UserSession",
    "build_engine",
    "create_schema",
    "get_engine",
    "get_session",
    "session_scope",
]
