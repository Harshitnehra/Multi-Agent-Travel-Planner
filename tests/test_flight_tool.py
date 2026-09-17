import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.flight_tool import get_default_origin_iata, get_flight_status, parse_route


class FlightRouteTests(unittest.TestCase):
    def test_destination_before_origin(self):
        self.assertEqual(
            parse_route("Plan a 7 days Japan trip from Bangladesh"),
            ("DAC", "NRT"),
        )

    def test_city_destination_before_origin_with_trailing_requirements(self):
        self.assertEqual(
            parse_route(
                "Plan a 5 days Dubai trip from Dhaka with flights, hotels and sightseeing."
            ),
            ("DAC", "DXB"),
        )

    def test_explicit_from_to_route(self):
        self.assertEqual(parse_route("Flights from Dhaka to Tokyo"), ("DAC", "NRT"))

    def test_single_destination_uses_default_origin(self):
        with patch.dict(os.environ, {"DEFAULT_ORIGIN_IATA": "DEL"}):
            self.assertEqual(parse_route("Tokyo trip"), ("DEL", "NRT"))

    def test_invalid_default_origin_falls_back_to_delhi(self):
        with patch.dict(os.environ, {"DEFAULT_ORIGIN_IATA": "NOT-AIRPORT"}):
            self.assertEqual(get_default_origin_iata(), "DEL")

    @patch("tools.flight_tool.requests.get")
    @patch("tools.flight_tool.get_settings")
    def test_no_live_match_returns_provider_result_instead_of_raising(
        self, settings, request
    ):
        settings.return_value = SimpleNamespace(
            aviationstack_api_key="test-key", request_timeout_seconds=5
        )
        response = request.return_value
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": []}

        result = get_flight_status("6E2134", "2026-09-17")

        self.assertIn("Status: unavailable", result)
        self.assertIn("provider was reached successfully", result)


if __name__ == "__main__":
    unittest.main()
