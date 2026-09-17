import unittest
import uuid
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from unittest.mock import Mock, patch

from database import Base, Booking, PaymentEvent, Trip, TripVersion, User
from database import build_engine, session_scope
from services.demo_booking_service import (
    DEMO_NOTICE,
    DemoBookingConflict,
    DemoBookingService,
    DemoOfferExpired,
)


def valid_plan():
    return {
        "title": "Delhi to Dubai demo",
        "original_request": "Plan a Dubai trip",
        "origin": {"name": "Delhi", "city": "Delhi", "iata_code": "DEL"},
        "destination": {"name": "Dubai", "city": "Dubai", "iata_code": "DXB"},
        "start_date": "2026-10-12",
        "end_date": "2026-10-16",
        "timezone": "Asia/Dubai",
        "currency": "INR",
        "flights": [
            {
                "segment_id": "flight-original",
                "airline": "Suggested airline",
                "origin": {"name": "Delhi", "iata_code": "DEL"},
                "destination": {"name": "Dubai", "iata_code": "DXB"},
                "departure_at": "2026-10-12T09:00:00+05:30",
                "arrival_at": "2026-10-12T12:00:00+04:00",
                "estimated_cost": "20000",
                "currency": "INR",
            }
        ],
        "hotels": [
            {
                "stay_id": "hotel-original",
                "name": "Suggested hotel",
                "city": "Dubai",
                "check_in": "2026-10-12",
                "check_out": "2026-10-16",
                "estimated_cost": "30000",
                "currency": "INR",
            }
        ],
    }


class DemoBookingTests(unittest.TestCase):
    def setUp(self):
        self.payment_environment = patch.dict(
            os.environ, {"RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": ""}
        )
        self.payment_environment.start()
        self.engine = build_engine("sqlite://", shared_memory=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.payment_environment.stop()

    def add_trip(self, session):
        unique = uuid.uuid4().hex
        user = User(
            email=f"{unique}@example.com",
            password_hash="test-only",
            display_name="Demo User",
        )
        trip = Trip(
            user=user,
            planning_thread_id=f"thread-{unique}",
            title="Demo trip",
            status="approved",
            currency="INR",
        )
        trip.versions.append(
            TripVersion(
                version_number=1,
                structured_plan=valid_plan(),
                rendered_itinerary="Approved itinerary",
                change_reason="Initial",
                created_by="planning_agent",
            )
        )
        session.add(trip)
        session.flush()
        return trip

    def create_held_offer(self, session, booking_type="flight"):
        trip = self.add_trip(session)
        service = DemoBookingService(session)
        offers = service.search_offers(trip, booking_type)
        held = service.hold_offer(trip, offers[0]["id"])
        return trip, service, held

    def test_user_can_search_select_and_hold_demo_offers(self):
        with session_scope(self.engine) as session:
            trip = self.add_trip(session)
            service = DemoBookingService(session)
            offers = service.search_offers(trip, "hotel")
            self.assertEqual(len(offers), 3)
            self.assertTrue(all(item["demo"] for item in offers))
            self.assertTrue(all(item["notice"] == DEMO_NOTICE for item in offers))

            held = service.hold_offer(trip, offers[1]["id"])
            self.assertEqual(held["status"], "held")
            self.assertIn("held_until", held["details"])

    def test_dummy_flight_form_details_are_saved_with_offer_and_trip_item(self):
        details = {
            "passenger_name": "Demo Traveler",
            "origin": "Delhi",
            "origin_iata": "DEL",
            "destination": "Dubai",
            "destination_iata": "DXB",
            "departure_date": "2026-11-10",
            "passengers": 2,
            "cabin": "economy",
        }
        with session_scope(self.engine) as session:
            trip = self.add_trip(session)
            offer = DemoBookingService(session).search_offers(trip, "flight", details)[0]

            self.assertEqual(offer["details"]["search_details"], details)
            item = offer["details"]["trip_item"]
            self.assertEqual(item["origin"]["iata_code"], "DEL")
            self.assertEqual(item["destination"]["iata_code"], "DXB")
            self.assertTrue(item["departure_at"].startswith("2026-11-10"))

    def test_expired_hold_cannot_create_payment(self):
        with session_scope(self.engine) as session:
            trip, service, held = self.create_held_offer(session)
            booking = session.get(Booking, held["id"])
            details = dict(booking.details)
            details["held_until"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            booking.details = details

            with self.assertRaises(DemoOfferExpired):
                service.create_payment(trip, booking.id, "expired-checkout-key")
            self.assertEqual(booking.status, "expired")

    def test_success_confirms_demo_item_and_duplicate_event_is_idempotent(self):
        with session_scope(self.engine) as session:
            trip, service, held = self.create_held_offer(session)
            payment = service.create_payment(trip, held["id"], "success-checkout-key")
            first = service.simulate_payment(
                trip, held["id"], payment["id"], "event-success-1", "success"
            )
            duplicate = service.simulate_payment(
                trip, held["id"], payment["id"], "event-success-1", "success"
            )

            self.assertEqual(first["booking"]["status"], "demo_confirmed")
            self.assertFalse(first["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(trip.current_version, 2)
            plan = max(trip.versions, key=lambda item: item.version_number).structured_plan
            confirmed = next(item for item in plan["flights"] if item["booking_id"] == held["id"])
            self.assertTrue(confirmed["demo_booking"])
            self.assertEqual(confirmed["status"], "booked")
            self.assertEqual(session.scalar(select(func.count(PaymentEvent.id))), 1)

    def test_failed_and_cancelled_outcomes_are_simulated(self):
        for outcome in ("failed", "cancelled"):
            with self.subTest(outcome=outcome), session_scope(self.engine) as session:
                trip, service, held = self.create_held_offer(session)
                payment = service.create_payment(
                    trip, held["id"], f"{outcome}-checkout-key"
                )
                result = service.simulate_payment(
                    trip,
                    held["id"],
                    payment["id"],
                    f"event-{outcome}",
                    outcome,
                )
                self.assertEqual(result["payment"]["status"], outcome)

    def test_payment_cannot_confirm_a_different_booking(self):
        with session_scope(self.engine) as session:
            trip = self.add_trip(session)
            service = DemoBookingService(session)
            offers = service.search_offers(trip, "flight")
            first = service.hold_offer(trip, offers[0]["id"])
            second = service.hold_offer(trip, offers[1]["id"])
            payment = service.create_payment(trip, first["id"], "bound-checkout-key")

            with self.assertRaisesRegex(DemoBookingConflict, "does not belong"):
                service.simulate_payment(
                    trip, second["id"], payment["id"], "event-wrong-booking", "success"
                )
            self.assertNotEqual(session.get(Booking, second["id"]).status, "demo_confirmed")

    def test_verified_razorpay_test_payment_confirms_only_bound_booking(self):
        gateway = Mock()
        gateway.create_order.return_value = {
            "order_id": "order_provider_123",
            "key_id": "rzp_test_public",
            "amount": 1800000,
            "currency": "INR",
        }
        gateway.verify_signature.return_value = True
        with session_scope(self.engine) as session:
            trip = self.add_trip(session)
            service = DemoBookingService(session, gateway=gateway)
            offer = service.search_offers(trip, "flight")[0]
            held = service.hold_offer(trip, offer["id"])
            payment = service.create_payment(trip, held["id"], "razorpay-checkout-key")
            result = service.verify_razorpay_payment(
                trip,
                held["id"],
                payment["id"],
                razorpay_order_id="order_provider_123",
                razorpay_payment_id="pay_provider_123",
                razorpay_signature="valid-signature",
                event_id="razorpay-pay_provider_123",
            )
            self.assertEqual(result["booking"]["status"], "demo_confirmed")
            self.assertEqual(result["payment"]["status"], "succeeded")
            gateway.verify_signature.assert_called_once_with(
                "order_provider_123", "pay_provider_123", "valid-signature"
            )


if __name__ == "__main__":
    unittest.main()
