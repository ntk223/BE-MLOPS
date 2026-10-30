"""Advisor API integration tests against the demo PostgreSQL.

Skipped when the database is unreachable so the suite stays green.
"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from learnguard_backend.api.app import create_app
from learnguard_backend.db.session import create_session_factory
from learnguard_backend.settings import BackendSettings

TEST_DATABASE_URL = "postgresql://learnguard:learnguard@localhost:5432/learnguard"


def _database_available() -> bool:
    try:
        factory = create_session_factory(TEST_DATABASE_URL)
        with factory() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _database_available()

SETTINGS = BackendSettings(database_url=TEST_DATABASE_URL)


@unittest.skipUnless(DB_AVAILABLE, "demo PostgreSQL not reachable")
class PredictionEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app(SETTINGS))

    def test_list_top50_scored_desc(self) -> None:
        """AC3: 50 rows, risk_score strictly non-increasing."""
        response = self.client.get(
            "/predictions",
            params={"status": "scored", "order_by": "risk_score", "limit": 50},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["limit"], 50)
        self.assertEqual(len(body["items"]), 50)
        scores = [item["risk_score"] for item in body["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_total_is_6510(self) -> None:
        body = self.client.get("/predictions", params={"limit": 1}).json()
        self.assertEqual(body["total"], 6510)

    def test_null_label_serializes_to_null(self) -> None:
        """AC4: insufficient_data rows -> predicted_label is JSON null."""
        response = self.client.get(
            "/predictions", params={"status": "insufficient_data", "limit": 5}
        )
        self.assertEqual(response.status_code, 200)
        for item in response.json()["items"]:
            self.assertIsNone(item["predicted_label"])
            self.assertEqual(item["status"], "insufficient_data")

    def test_detail_endpoint_returns_parsed_signals(self) -> None:
        listed = self.client.get(
            "/predictions",
            params={"status": "scored", "limit": 1, "order_by": "risk_score"},
        ).json()
        item = listed["items"][0]
        detail = self.client.get(
            "/predictions/{}/{}/{}".format(
                item["code_module"],
                item["code_presentation"],
                item["student_key"],
            )
        )
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["student_key"], item["student_key"])
        self.assertTrue(all("feature_name" in s for s in body["top_signals"]))

    def test_detail_404_for_unknown_key(self) -> None:
        response = self.client.get("/predictions/ZZZ/2099Z/0000000000000000")
        self.assertEqual(response.status_code, 404)

    def test_invalid_status_rejected_with_400(self) -> None:
        response = self.client.get("/predictions", params={"status": "hacked"})
        self.assertEqual(response.status_code, 400)

    def test_audit_log_written_for_access(self) -> None:
        self.client.get("/predictions", params={"limit": 1})
        factory = create_session_factory(TEST_DATABASE_URL)
        with factory() as session:
            row = session.execute(
                text(
                    "SELECT action, entity FROM audit_logs "
                    "ORDER BY at DESC LIMIT 1"
                )
            ).one()
        self.assertEqual(row.action, "prediction_list")
        self.assertEqual(row.entity, "ml_prediction")


class PredictionEndpointNoDbTests(unittest.TestCase):
    def test_503_without_database_url(self) -> None:
        client = TestClient(create_app(BackendSettings()))
        response = client.get("/predictions")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
