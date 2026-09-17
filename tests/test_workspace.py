import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from database import AuditEvent, Base, Booking, MonitorSnapshot, Trip, TripVersion, User
from database import build_engine, session_scope
from services.workspace_service import (
    WorkspaceNotReady,
    WorkspaceProviderError,
    WorkspaceService,
    _flight_risk,
    _flight_result,
    _weather_risk,
    _weather_result,
    workspace_payload,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.engine = build_engine("sqlite://", shared_memory=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def add_trip(self, session, plan):
        user = User(
            email="workspace@example.com",
            password_hash="test-only",
            display_name="Workspace User",
        )
        trip = Trip(
            user=user,
            planning_thread_id="workspace-thread",
            title="Workspace trip",
            status="approved",
            currency="INR",
        )
        trip.versions.append(
            TripVersion(
                version_number=1,
                structured_plan=plan,
                rendered_itinerary="Saved itinerary",
                change_reason="Initial",
                created_by="planning_agent",
            )
        )
        session.add(trip)
        session.flush()
        return trip

    def test_flight_status_requires_number_and_departure_date(self):
        plans = [
            {"flights": [], "destination": {"city": "Dubai"}},
            {
                "flights": [{"flight_number": "AI171", "departure_at": None}],
                "destination": {"city": "Dubai"},
            },
        ]
        for plan in plans:
            with self.subTest(plan=plan), session_scope(self.engine) as session:
                trip = self.add_trip(session, plan)
                capability = workspace_payload(trip)["actions"]["check_flight"]
                self.assertFalse(capability["enabled"])
                self.assertIn("flight number and departure date", capability["reason"])
                session.rollback()

        with session_scope(self.engine) as session:
            trip = self.add_trip(
                session,
                {
                    "flights": [
                        {"flight_number": "AI171", "departure_at": "2026-10-12T08:00:00+05:30"}
                    ],
                    "destination": {"city": "Dubai"},
                },
            )
            self.assertTrue(workspace_payload(trip)["actions"]["check_flight"]["enabled"])

    @patch("services.workspace_service.call_mcp_tool")
    def test_weather_monitoring_needs_no_flight_or_booking(self, call_tool):
        call_tool.return_value = {"city": "Dubai", "current": {"temperature_c": 34}}
        with session_scope(self.engine) as session:
            trip = self.add_trip(session, {"flights": [], "destination": {"city": "Dubai"}})
            result = WorkspaceService(session).run_action(trip, "check_weather")

            self.assertEqual(result["state"], "success")
            self.assertEqual(session.scalar(select(func.count(Booking.id))), 0)
            self.assertEqual(session.scalar(select(func.count(MonitorSnapshot.id))), 1)
            self.assertEqual(session.scalar(select(func.count(AuditEvent.id))), 1)
        call_tool.assert_called_once_with("weather_for_city", {"city": "Dubai"})

    @patch("services.workspace_service.call_mcp_tool")
    def test_disabled_flight_action_never_calls_provider(self, call_tool):
        with session_scope(self.engine) as session:
            trip = self.add_trip(session, {"destination": {"city": "Dubai"}})
            with self.assertRaises(WorkspaceNotReady):
                WorkspaceService(session).run_action(trip, "check_flight")
        call_tool.assert_not_called()

    @patch("services.workspace_service.call_mcp_tool")
    def test_provider_no_match_is_saved_as_result_instead_of_502(self, call_tool):
        call_tool.return_value = (
            "Flight: 6E2134\nDate: 2026-09-17\nStatus: unavailable\n"
            "Source: AviationStack real-time flight data"
        )
        with session_scope(self.engine) as session:
            trip = self.add_trip(
                session,
                {
                    "flights": [
                        {
                            "flight_number": "6E2134",
                            "departure_at": "2026-09-17T00:00:00+00:00",
                        }
                    ],
                    "destination": {"city": "Dubai"},
                },
            )
            result = WorkspaceService(session).run_action(trip, "check_flight")
            snapshot = session.scalar(select(MonitorSnapshot))

            self.assertEqual(result["state"], "no_data")
            self.assertIn("no live status", result["message"])
            self.assertEqual(snapshot.source_status["flight"], "no_live_match")

    @patch("services.workspace_service.call_mcp_tool", side_effect=RuntimeError("provider down"))
    def test_provider_failure_becomes_workspace_error(self, _call_tool):
        with session_scope(self.engine) as session:
            trip = self.add_trip(session, {"destination": {"city": "Dubai"}})
            with self.assertRaisesRegex(WorkspaceProviderError, "Weather could not be checked"):
                WorkspaceService(session).run_action(trip, "check_weather")
            self.assertEqual(session.scalar(select(func.count(MonitorSnapshot.id))), 0)

    def test_user_can_add_minimum_flight_monitoring_details(self):
        with session_scope(self.engine) as session:
            trip = self.add_trip(
                session,
                {
                    "title": "Dubai trip",
                    "original_request": "Plan a Dubai trip from Delhi",
                    "currency": "INR",
                    "origin": {"name": "Delhi", "city": "Delhi"},
                    "destination": {"name": "Dubai", "city": "Dubai"},
                    "flights": [],
                },
            )
            self.assertFalse(workspace_payload(trip)["actions"]["check_flight"]["enabled"])
            workspace = WorkspaceService(session).add_flight_details(
                trip, "ai-171", "2026-10-12"
            )
            self.assertTrue(workspace["actions"]["check_flight"]["enabled"])
            self.assertEqual(workspace["monitoring"]["flight"]["flight_number"], "AI171")
            self.assertEqual(trip.current_version, 2)

    def test_flight_provider_text_is_normalized_for_the_ui(self):
        result = _flight_result(
            {
                "result": (
                    "Airline: Air India\nFlight: AI171\nStatus: active\n"
                    "Departure:\n- Airport: Indira Gandhi International\n"
                    "- IATA: DEL\n- Terminal: 3\n- Gate: 12\n"
                    "- Scheduled: 2026-10-12T08:00:00+00:00\n- Delay: 15 min\n"
                    "Arrival:\n- Airport: Dubai International\n- IATA: DXB\n"
                    "- Terminal: 1\n- Gate: B4\n"
                    "- Scheduled: 2026-10-12T11:00:00+00:00\n- Delay: 5 min"
                )
            },
            {"flight_number": "AI171", "flight_date": "2026-10-12"},
        )

        self.assertEqual(result["kind"], "flight_status")
        self.assertEqual(result["availability"], "available")
        self.assertEqual(result["matches"][0]["departure"]["iata"], "DEL")
        self.assertEqual(result["matches"][0]["arrival"]["gate"], "B4")

    def test_weather_provider_json_is_normalized_for_the_ui(self):
        result = _weather_result(
            {
                "result": (
                    '{"city":"Dubai","current":{"temperature_c":34,'
                    '"feels_like_c":38,"humidity":45,"condition":"Clear",'
                    '"wind_speed":4.2},"forecast":{"forecast":['
                    '{"datetime":"2026-10-12 12:00","temperature_c":35,'
                    '"condition":"Sunny"}]}}'
                )
            },
            "Dubai",
        )

        self.assertEqual(result["kind"], "weather")
        self.assertEqual(result["current"]["temperature_c"], 34)
        self.assertEqual(result["forecast"][0]["condition"], "Sunny")

    def test_delayed_or_cancelled_flight_triggers_recovery_choice(self):
        risk = _flight_risk(
            {
                "query": {"flight_number": "AI171"},
                "matches": [
                    {
                        "flight": "AI171",
                        "status": "cancelled",
                        "departure": {"delay": "45 minutes"},
                        "arrival": {},
                    }
                ],
            }
        )

        self.assertTrue(risk["detected"])
        self.assertEqual(risk["severity"], "critical")
        self.assertIn("recreate", risk["prompt"].lower())

    def test_rough_weather_triggers_recovery_choice(self):
        risk = _weather_risk(
            {
                "current": {
                    "condition": "Thunderstorm with heavy rain",
                    "wind_speed": 12.5,
                },
                "forecast": [],
            }
        )

        self.assertTrue(risk["detected"])
        self.assertEqual(risk["category"], "weather")
        self.assertGreaterEqual(len(risk["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
