import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"

import app
from database import Base, build_engine, session_scope


def workflow_result(status: str = "approval_required") -> dict:
    return {
        "thread_id": "trip-thread-1",
        "status": status,
        "answer": "Draft plan",
        "itinerary": "Draft plan",
        "structured_plan": {
            "title": "Japan trip",
            "original_request": "Plan a Japan trip",
            "currency": "INR",
            "destination": {"name": "Tokyo", "city": "Tokyo"},
        },
        "approval": {"allowed_actions": ["approve", "revise", "reject"]}
        if status == "approval_required"
        else None,
    }


class AppTests(unittest.TestCase):
    def setUp(self):
        self.payment_environment = patch.dict(
            os.environ, {"RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": ""}
        )
        self.payment_environment.start()
        self.engine = build_engine("sqlite://", shared_memory=True)
        Base.metadata.create_all(self.engine)

        def test_session():
            with session_scope(self.engine) as session:
                yield session

        app.app.dependency_overrides[app.get_session] = test_session
        self.client = TestClient(app.app)

    def tearDown(self):
        app.app.dependency_overrides.clear()
        self.engine.dispose()
        self.payment_environment.stop()

    def register(self, email: str = "traveler@example.com"):
        return self.client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "correct-horse-battery",
                "display_name": "Traveler",
            },
        )

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_workspace_ui_has_loading_success_and_error_states(self):
        page = self.client.get("/").text
        script = self.client.get("/static/script.js").text
        for action in ("check_flight", "check_weather"):
            self.assertIn(f'id="action-{action}"', page)
        self.assertNotIn('id="action-book_flight"', page)
        self.assertNotIn('id="action-book_hotel"', page)
        self.assertNotIn('id="action-skip_booking"', page)
        self.assertNotIn('skip_booking: "Skip booking"', script)
        self.assertIn('setWorkspaceActionState(action, "loading"', script)
        self.assertIn('noData || riskDetected ? "warning" : "success"', script)
        self.assertIn('setWorkspaceActionState(action, "error"', script)
        self.assertNotIn("DEMO BOOKING", page)
        self.assertNotIn('id="demoFlightBookingForm"', page)
        self.assertIn('id="appToast"', page)
        self.assertNotIn("openDemoFlightBookingForm", script)
        self.assertNotIn("searchDemoOffers", script)
        self.assertIn("openFlightStatusForm", script)
        self.assertIn("Check real status", page)
        self.assertIn("Real flight status retrieved successfully", script)
        self.assertIn('id="riskDecision"', page)
        self.assertIn("Recreate trip", page)
        self.assertIn("Skip — keep current trip", page)
        self.assertIn("handleRiskDecision", script)

    def test_demo_payment_api_rejects_real_payment_credentials(self):
        self.register()
        response = self.client.post(
            "/api/trips/trip-1/demo-bookings/booking-1/payments",
            json={
                "idempotency_key": "checkout-key-123456",
                "card_number": "4111111111111111",
                "cvv": "123",
                "razorpay_key_secret": "must-not-be-accepted",
            },
        )
        self.assertEqual(response.status_code, 422)

    @patch("app.start_travel_plan")
    def test_demo_hotel_checkout_api_flow_is_idempotent(self, start_plan):
        self.register()
        start_plan.return_value = workflow_result("completed")
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        trip_id = created["trip_id"]

        offers = self.client.post(
            f"/api/trips/{trip_id}/demo-offers/search",
            json={"booking_type": "hotel"},
        ).json()["offers"]
        booking = self.client.post(
            f"/api/trips/{trip_id}/demo-offers/{offers[0]['id']}/hold", json={}
        ).json()["booking"]
        payment = self.client.post(
            f"/api/trips/{trip_id}/demo-bookings/{booking['id']}/payments",
            json={"idempotency_key": "api-checkout-key-123"},
        ).json()["payment"]
        payment_url = (
            f"/api/trips/{trip_id}/demo-bookings/{booking['id']}"
            f"/payments/{payment['id']}/simulate"
        )
        first = self.client.post(
            payment_url, json={"event_id": "api-event-success", "outcome": "success"}
        ).json()
        duplicate = self.client.post(
            payment_url, json={"event_id": "api-event-success", "outcome": "success"}
        ).json()

        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        trip = self.client.get(f"/api/trips/{trip_id}").json()["trip"]
        self.assertEqual(trip["current_version"], 2)
        self.assertTrue(trip["structured_plan"]["hotels"][0]["demo_booking"])

    @patch("app.start_travel_plan")
    def test_dummy_flight_form_api_creates_personalized_demo_offers(self, start_plan):
        self.register()
        start_plan.return_value = workflow_result("completed")
        trip_id = self.client.post(
            "/api/travel", json={"message": "Plan a Dubai trip from Delhi"}
        ).json()["trip_id"]

        response = self.client.post(
            f"/api/trips/{trip_id}/demo-offers/search",
            json={
                "booking_type": "flight",
                "flight_details": {
                    "passenger_name": "Demo Traveler",
                    "origin": "Delhi",
                    "origin_iata": "DEL",
                    "destination": "Dubai",
                    "destination_iata": "DXB",
                    "departure_date": "2026-11-10",
                    "passengers": 1,
                    "cabin": "economy",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        offer = response.json()["offers"][0]
        self.assertTrue(offer["demo"])
        self.assertEqual(
            offer["details"]["search_details"]["passenger_name"], "Demo Traveler"
        )
        self.assertEqual(
            offer["details"]["trip_item"]["destination"]["iata_code"], "DXB"
        )

    def test_auth_ui_uses_a_real_form_and_explains_validation_errors(self):
        page = self.client.get("/").text
        script = self.client.get("/static/script.js").text
        self.assertIn('id="authForm"', page)
        self.assertIn('type="submit"', page)
        self.assertIn('id="authMessage"', page)
        self.assertIn('apiErrorMessage', script)

    def test_registration_validation_returns_readable_details(self):
        response = self.client.post(
            "/api/auth/register",
            json={"email": "bad", "password": "short", "display_name": ""},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], list)

    def test_travel_requires_authentication(self):
        response = self.client.post("/api/travel", json={"message": "Plan Japan"})
        self.assertEqual(response.status_code, 401)

    def test_register_me_logout_and_login(self):
        response = self.register("Traveler@Example.COM")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["email"], "traveler@example.com")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

        response = self.client.post(
            "/api/auth/login",
            json={"email": "traveler@example.com", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_duplicate_account_and_bad_password_are_rejected(self):
        self.register()
        self.client.post("/api/auth/logout")
        self.assertEqual(self.register().status_code, 409)
        response = self.client.post(
            "/api/auth/login",
            json={"email": "traveler@example.com", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 401)

    @patch("app.start_travel_plan")
    def test_start_persists_resumable_trip(self, start_plan):
        self.register()
        start_plan.return_value = workflow_result()

        response = self.client.post("/api/travel", json={"message": "Plan a Japan trip"})
        self.assertEqual(response.status_code, 200)
        trip_id = response.json()["trip_id"]

        listing = self.client.get("/api/trips").json()["trips"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], trip_id)
        self.assertEqual(listing[0]["status"], "paused")

        detail = self.client.get(f"/api/trips/{trip_id}").json()["trip"]
        self.assertEqual(detail["itinerary"], "Draft plan")
        self.assertEqual(detail["messages"][0]["content"], "Plan a Japan trip")

    @patch("app.resume_travel_plan")
    @patch("app.start_travel_plan")
    def test_approval_updates_owned_saved_trip(self, start_plan, resume_plan):
        self.register()
        start_plan.return_value = workflow_result()
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        resume_plan.return_value = workflow_result("completed")

        response = self.client.post(
            "/api/travel/approval",
            json={"thread_id": created["thread_id"], "action": "approve", "feedback": ""},
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/api/trips/{created['trip_id']}").json()["trip"]
        self.assertEqual(detail["status"], "approved")
        self.assertEqual(detail["messages"][-1]["message_type"], "approval_approve")

    @patch("app.resume_travel_plan")
    @patch("app.start_travel_plan")
    def test_revision_creates_a_new_immutable_version(self, start_plan, resume_plan):
        self.register()
        start_plan.return_value = workflow_result()
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        revised = workflow_result()
        revised["answer"] = revised["itinerary"] = "Revised plan"
        revised["structured_plan"] = {
            **revised["structured_plan"],
            "title": "Revised Japan trip",
        }
        resume_plan.return_value = revised

        response = self.client.post(
            "/api/travel/approval",
            json={
                "thread_id": created["thread_id"],
                "action": "revise",
                "feedback": "Use one hotel.",
            },
        )

        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/api/trips/{created['trip_id']}").json()["trip"]
        self.assertEqual(detail["current_version"], 2)
        self.assertEqual([item["itinerary"] for item in detail["versions"]], ["Draft plan", "Revised plan"])
        self.assertEqual(detail["versions"][1]["change_reason"], "Use one hotel.")

    def test_users_cannot_open_each_others_trips(self):
        with patch("app.start_travel_plan", return_value=workflow_result()):
            self.register("first@example.com")
            created = self.client.post(
                "/api/travel", json={"message": "Plan a Japan trip"}
            ).json()
        self.client.post("/api/auth/logout")
        self.register("second@example.com")
        self.assertEqual(self.client.get(f"/api/trips/{created['trip_id']}").status_code, 404)

    @patch("services.workspace_service.call_mcp_tool")
    @patch("app.start_travel_plan")
    def test_workspace_keeps_weather_independent_from_flight(self, start_plan, call_tool):
        self.register()
        start_plan.return_value = workflow_result("completed")
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()

        workspace = self.client.get(
            f"/api/trips/{created['trip_id']}/workspace"
        ).json()["workspace"]
        self.assertFalse(workspace["actions"]["check_flight"]["enabled"])
        self.assertTrue(workspace["actions"]["check_weather"]["enabled"])

        blocked = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_flight",
            json={},
        )
        self.assertEqual(blocked.status_code, 409)

        call_tool.return_value = {"city": "Tokyo", "current": {"temperature_c": 24}}
        weather = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_weather",
            json={},
        )
        self.assertEqual(weather.status_code, 200)
        self.assertEqual(weather.json()["state"], "success")
        call_tool.assert_called_once_with("weather_for_city", {"city": "Tokyo"})

    @patch("app.start_travel_plan")
    def test_flight_details_accept_common_flight_number_separators(self, start_plan):
        self.register()
        start_plan.return_value = workflow_result("completed")
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()

        response = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/flight-details",
            json={"flight_number": "AI-171", "flight_date": "2026-10-12"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["workspace"]["monitoring"]["flight"]["flight_number"],
            "AI171",
        )

    @patch("services.workspace_service.call_mcp_tool")
    @patch("app.start_travel_plan")
    def test_user_can_skip_detected_weather_risk(self, start_plan, call_tool):
        self.register()
        start_plan.return_value = workflow_result("completed")
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        call_tool.return_value = {
            "city": "Tokyo",
            "current": {"condition": "Thunderstorm", "wind_speed": 13},
        }

        checked = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_weather",
            json={},
        )
        decision = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/risk-decision",
            json={"decision": "skip"},
        )

        self.assertEqual(checked.json()["state"], "risk")
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["decision"], "skip")

    @patch("services.workspace_service.call_mcp_tool")
    @patch("app.start_travel_plan")
    def test_recreate_after_risk_returns_new_draft_for_approval(self, start_plan, call_tool):
        self.register()
        replacement = workflow_result("approval_required")
        replacement["answer"] = replacement["itinerary"] = "Recovery itinerary"
        replacement["structured_plan"] = {
            **replacement["structured_plan"],
            "summary": "Recreated for rough weather",
        }
        start_plan.side_effect = [workflow_result("completed"), replacement]
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        call_tool.return_value = {
            "city": "Tokyo",
            "current": {"condition": "Severe thunderstorm", "wind_speed": 15},
        }
        self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_weather",
            json={},
        )

        response = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/risk-decision",
            json={"decision": "recreate"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approval_required")
        detail = self.client.get(f"/api/trips/{created['trip_id']}").json()["trip"]
        self.assertEqual(detail["status"], "paused")
        self.assertEqual(detail["itinerary"], "Recovery itinerary")

    def test_local_recovery_removes_disrupted_flight_when_provider_fails(self):
        latest = SimpleNamespace(
            structured_plan={
                "title": "Thailand trip",
                "original_request": "Plan Thailand from Mumbai",
                "summary": "Approved plan",
                "flights": [
                    {"flight_number": "6E1059", "status": "suggested"},
                    {"flight_number": "AI330", "status": "suggested"},
                ],
                "assumptions": [],
                "missing_information": [],
            },
            rendered_itinerary="# Thailand trip\n\nUse flight 6E1059 to Bangkok.",
        )
        risk = {
            "category": "flight",
            "title": "Flight disruption detected",
            "reasons": ["Flight 6E1059 is cancelled."],
        }

        result = app._local_recovery_result(latest, risk)

        self.assertEqual(result["status"], "approval_required")
        self.assertTrue(result["thread_id"].startswith("recovery_"))
        self.assertEqual(
            [item["flight_number"] for item in result["structured_plan"]["flights"]],
            ["AI330"],
        )
        self.assertNotIn("6E1059 to Bangkok", result["itinerary"])
        self.assertIn("alternative flight", result["itinerary"])

    @patch("services.workspace_service.call_mcp_tool")
    @patch("app.start_travel_plan")
    def test_recreate_uses_local_recovery_when_ai_provider_falls_back(
        self, start_plan, call_tool
    ):
        self.register()
        fallback = workflow_result("approval_required")
        fallback["answer"] = fallback["itinerary"] = (
            "The AI planning provider is temporarily unavailable."
        )
        fallback["structured_plan"] = {
            **fallback["structured_plan"],
            "summary": "Structured details could not be extracted from this draft.",
        }
        start_plan.side_effect = [workflow_result("completed"), fallback]
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        call_tool.return_value = {
            "city": "Tokyo",
            "current": {"condition": "Thunderstorm", "wind_speed": 14},
        }
        self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_weather",
            json={},
        )

        response = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/risk-decision",
            json={"decision": "recreate"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["thread_id"].startswith("recovery_"))
        self.assertIn("Required recovery changes", response.json()["itinerary"])
        self.assertNotIn("temporarily unavailable", response.json()["itinerary"])

    @patch("services.workspace_service.call_mcp_tool")
    @patch("app.start_travel_plan")
    def test_recreate_still_works_when_external_planner_raises(
        self, start_plan, call_tool
    ):
        self.register()
        start_plan.side_effect = [workflow_result("completed"), RuntimeError("offline")]
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()
        call_tool.return_value = {
            "city": "Tokyo",
            "current": {"condition": "Thunderstorm", "wind_speed": 14},
        }
        self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/actions/check_weather",
            json={},
        )

        response = self.client.post(
            f"/api/trips/{created['trip_id']}/workspace/risk-decision",
            json={"decision": "recreate"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approval_required")
        self.assertTrue(response.json()["thread_id"].startswith("recovery_"))

    @patch("app.resume_travel_plan", side_effect=RuntimeError("checkpoint missing"))
    @patch("app.start_travel_plan")
    def test_saved_draft_can_be_approved_after_checkpoint_loss(self, start_plan, _resume):
        self.register()
        start_plan.return_value = workflow_result()
        created = self.client.post(
            "/api/travel", json={"message": "Plan a Japan trip"}
        ).json()

        response = self.client.post(
            "/api/travel/approval",
            json={"thread_id": created["thread_id"], "action": "approve", "feedback": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        detail = self.client.get(f"/api/trips/{created['trip_id']}").json()["trip"]
        self.assertEqual(detail["status"], "approved")


if __name__ == "__main__":
    unittest.main()
