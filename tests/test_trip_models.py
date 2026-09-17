import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import ValidationError

from domain import Activity, FlightSegment, HotelStay, Location, TripPlan


class TripModelTests(unittest.TestCase):
    def test_plan_serializes_dates_and_money_for_api_json(self):
        plan = TripPlan(
            title="Dubai getaway",
            original_request="Plan a five-day Dubai trip from Delhi",
            origin=Location(name="Delhi", iata_code="del"),
            destination=Location(name="Dubai", iata_code="dxb"),
            start_date=date(2026, 10, 12),
            end_date=date(2026, 10, 16),
            currency="inr",
            total_budget=Decimal("80000.00"),
        )

        payload = plan.model_dump(mode="json")
        self.assertEqual(payload["origin"]["iata_code"], "DEL")
        self.assertEqual(payload["currency"], "INR")
        self.assertEqual(payload["start_date"], "2026-10-12")
        self.assertEqual(payload["total_budget"], "80000.00")

    def test_invalid_trip_date_order_is_rejected(self):
        with self.assertRaises(ValidationError):
            TripPlan(
                title="Invalid trip",
                original_request="Plan a trip",
                start_date=date(2026, 10, 16),
                end_date=date(2026, 10, 12),
            )

    def test_invalid_nested_time_ranges_are_rejected(self):
        later = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        earlier = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)

        with self.assertRaises(ValidationError):
            FlightSegment(departure_at=later, arrival_at=earlier)
        with self.assertRaises(ValidationError):
            Activity(name="Tour", starts_at=later, ends_at=earlier)
        with self.assertRaises(ValidationError):
            HotelStay(check_in=date(2026, 10, 15), check_out=date(2026, 10, 14))

    def test_naive_event_timestamps_are_rejected(self):
        naive = datetime(2026, 10, 12, 10)

        with self.assertRaises(ValidationError):
            FlightSegment(departure_at=naive)
        with self.assertRaises(ValidationError):
            Activity(name="Tour", starts_at=naive)

    def test_unknown_booking_details_can_remain_missing(self):
        plan = TripPlan(
            title="Dubai trip",
            original_request="Plan a Dubai trip",
            flights=[FlightSegment()],
            hotels=[HotelStay()],
            missing_information=["Flight number", "Travel dates"],
        )

        self.assertIsNone(plan.flights[0].flight_number)
        self.assertEqual(plan.flights[0].status, "suggested")
        self.assertFalse(plan.flights[0].source_verified)


if __name__ == "__main__":
    unittest.main()
