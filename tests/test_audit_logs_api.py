"""Admin audit-log API tests against the demo PostgreSQL.

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


def _count(factory, action=None, entity=None) -> int:
    statement = "SELECT COUNT(*) FROM audit_logs WHERE 1=1"
    if action is not None:
        statement += f" AND action = '{action}'"
    if entity is not None:
        statement += f" AND entity = '{entity}'"
    with factory() as session:
        return session.execute(text(statement)).scalar_one()


def _one_of(factory, column: str) -> str:
    with factory() as session:
        return session.execute(
            text(
                f"SELECT {column} FROM audit_logs "
                f"ORDER BY at DESC, log_id DESC LIMIT 1"
            )
        ).scalar_one()


@unittest.skipUnless(DB_AVAILABLE, "demo PostgreSQL not reachable")
class AuditLogEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app(SETTINGS))
        cls.factory = create_session_factory(TEST_DATABASE_URL)

    def test_list_returns_contract_shape(self) -> None:
        response = self.client.get("/audit-logs", params={"limit": 10})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertLessEqual(len(body["logs"]), 10)
        self.assertEqual(body["limit"], 10)
        # total counts ALL matching rows, not just the returned page.
        self.assertEqual(body["total"], _count(self.factory))
        self.assertGreater(body["total"], 0)
        self.assertEqual(body["actions"], sorted(body["actions"]))
        self.assertEqual(body["entities"], sorted(body["entities"]))
        self.assertGreater(len(body["actions"]), 0)
        self.assertGreater(len(body["entities"]), 0)
        log = body["logs"][0]
        for key in (
            "log_id", "at", "user_id", "actor_email", "actor_name",
            "action", "entity", "entity_id", "payload",
        ):
            self.assertIn(key, log)
        self.assertIsInstance(log["payload"], dict)

    def test_default_limit_is_50(self) -> None:
        body = self.client.get("/audit-logs").json()
        self.assertEqual(body["limit"], 50)
        self.assertLessEqual(len(body["logs"]), 50)

    def test_ordered_newest_first(self) -> None:
        body = self.client.get("/audit-logs", params={"limit": 20}).json()
        timestamps = [log["at"] for log in body["logs"]]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_filter_by_exact_action(self) -> None:
        action = _one_of(self.factory, "action")
        response = self.client.get("/audit-logs", params={"action": action})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], _count(self.factory, action=action))
        self.assertTrue(all(log["action"] == action for log in body["logs"]))
        self.assertTrue(len(body["logs"]) > 0)

    def test_filter_by_exact_entity(self) -> None:
        entity = _one_of(self.factory, "entity")
        response = self.client.get("/audit-logs", params={"entity": entity})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], _count(self.factory, entity=entity))
        self.assertTrue(all(log["entity"] == entity for log in body["logs"]))
        self.assertTrue(len(body["logs"]) > 0)

    def test_actor_fields_left_join_semantics(self) -> None:
        body = self.client.get("/audit-logs", params={"limit": 200}).json()
        for log in body["logs"]:
            if log["user_id"] is None:
                self.assertIsNone(log["actor_email"])
                self.assertIsNone(log["actor_name"])
            else:
                self.assertIsInstance(log["actor_email"], str)
                self.assertIsInstance(log["actor_name"], str)

    def test_limit_out_of_range_rejected_with_400(self) -> None:
        for limit in (0, 201):
            response = self.client.get("/audit-logs", params={"limit": limit})
            self.assertEqual(response.status_code, 400, f"limit={limit}")

    def test_reading_audit_logs_is_not_audited(self) -> None:
        before = _count(self.factory)
        response = self.client.get("/audit-logs", params={"limit": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_count(self.factory), before)

    def test_filter_with_no_match_returns_empty_page(self) -> None:
        response = self.client.get(
            "/audit-logs", params={"action": "no_such_action_xyz"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["logs"], [])
        self.assertEqual(body["total"], 0)


class AuditLogEndpointNoDbTests(unittest.TestCase):
    def test_503_without_database_url(self) -> None:
        client = TestClient(create_app(BackendSettings()))
        response = client.get("/audit-logs")
        self.assertEqual(response.status_code, 503)
        self.assertIn("database_not_configured", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
