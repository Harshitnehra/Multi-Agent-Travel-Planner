import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_health_does_not_require_database(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_empty_message_is_rejected(self):
        response = self.client.post("/api/travel", json={"message": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @patch("app.run_travel_agent")
    def test_travel_response(self, run_agent):
        run_agent.return_value = {
            "thread_id": "test-thread",
            "answer": "A plan",
            "flight_results": "Flights",
            "hotel_results": "Hotels",
            "itinerary": "Itinerary",
            "llm_calls": 2,
        }

        response = self.client.post("/api/travel", json={"message": "Japan trip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "A plan")


if __name__ == "__main__":
    unittest.main()
