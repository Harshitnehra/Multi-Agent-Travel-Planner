import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_empty_message_is_rejected(self):
        response = self.client.post("/api/travel", json={"message": "   "})
        self.assertEqual(response.status_code, 400)

    @patch("app.start_travel_plan")
    def test_start_returns_approval_payload(self, start_plan):
        start_plan.return_value = {
            "thread_id": "trip-1",
            "status": "approval_required",
            "answer": "Draft",
            "approval": {"allowed_actions": ["approve", "revise", "reject"]},
        }

        response = self.client.post("/api/travel", json={"message": "Plan a Japan trip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approval_required")

    @patch("app.resume_travel_plan")
    def test_approval_resumes_workflow(self, resume_plan):
        resume_plan.return_value = {
            "thread_id": "trip-1",
            "status": "completed",
            "answer": "Approved plan",
            "approval": None,
        }

        response = self.client.post(
            "/api/travel/approval",
            json={"thread_id": "trip-1", "action": "approve", "feedback": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Approved plan")


if __name__ == "__main__":
    unittest.main()
