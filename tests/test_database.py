import unittest
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from database import Base, Booking, Trip, TripVersion, User, build_engine, session_scope


EXPECTED_TABLES = {
    "audit_events",
    "bookings",
    "monitor_snapshots",
    "payment_orders",
    "payment_events",
    "recovery_proposals",
    "risk_events",
    "trip_messages",
    "trip_versions",
    "trips",
    "users",
    "user_sessions",
}


class DatabaseLayerTests(unittest.TestCase):
    def setUp(self):
        self.engine = build_engine("sqlite://", shared_memory=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _add_trip(self, session, thread_id: str = "trip-thread-1") -> Trip:
        user = User(
            email=f"{thread_id}@example.com",
            password_hash="not-a-real-password-hash",
            display_name="Traveler",
        )
        trip = Trip(
            user=user,
            planning_thread_id=thread_id,
            title="Delhi to Dubai",
            status="approved",
            currency="INR",
        )
        session.add(trip)
        session.flush()
        return trip

    def test_schema_contains_application_owned_tables(self):
        self.assertEqual(set(inspect(self.engine).get_table_names()), EXPECTED_TABLES)

    def test_trip_and_version_persist_together(self):
        with session_scope(self.engine) as session:
            trip = self._add_trip(session)
            trip_id = trip.id
            session.add(
                TripVersion(
                    trip=trip,
                    version_number=1,
                    structured_plan={"destination": "Dubai"},
                    rendered_itinerary="Five-day Dubai itinerary",
                    change_reason="Initial approved plan",
                    created_by="user",
                )
            )

        with session_scope(self.engine) as session:
            stored = session.scalar(select(Trip).where(Trip.id == trip_id))
            self.assertIsNotNone(stored)
            self.assertEqual(stored.versions[0].structured_plan["destination"], "Dubai")

    def test_money_is_stored_as_decimal(self):
        with session_scope(self.engine) as session:
            trip = self._add_trip(session, "trip-thread-money")
            booking = Booking(
                trip=trip,
                booking_type="flight",
                provider="mock",
                status="held",
                details={"flight_number": "DM101"},
                total_amount=Decimal("24500.50"),
                currency="INR",
                demo=True,
            )
            session.add(booking)
            session.flush()
            booking_id = booking.id

        with session_scope(self.engine) as session:
            stored = session.get(Booking, booking_id)
            self.assertEqual(stored.total_amount, Decimal("24500.50"))

    def test_transaction_rolls_back_on_constraint_error(self):
        with self.assertRaises(IntegrityError):
            with session_scope(self.engine) as session:
                trip = self._add_trip(session, "trip-thread-invalid")
                trip.status = "not-a-real-status"

        with session_scope(self.engine) as session:
            self.assertIsNone(
                session.scalar(
                    select(Trip).where(Trip.planning_thread_id == "trip-thread-invalid")
                )
            )

    def test_deleting_user_cascades_to_trip(self):
        with session_scope(self.engine) as session:
            trip = self._add_trip(session, "trip-thread-delete")
            user_id = trip.user.id
            trip_id = trip.id

        with session_scope(self.engine) as session:
            session.delete(session.get(User, user_id))

        with session_scope(self.engine) as session:
            self.assertIsNone(session.get(Trip, trip_id))


if __name__ == "__main__":
    unittest.main()
