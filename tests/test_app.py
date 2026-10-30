"""Smoke tests for the FastAPI app factory and health route."""

import unittest

from fastapi.testclient import TestClient

from learnguard_backend.api.app import API_TITLE, create_app
from learnguard_backend.settings import BackendSettings


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        # Defaults: no DATABASE_URL / JWT_SECRET / LLM_API_KEY configured.
        self.client = TestClient(create_app(BackendSettings()))

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["app"], API_TITLE)
        self.assertEqual(body["version"], "0.1.0")
        self.assertIn("dependencies", body)

    def test_health_reports_unconfigured_dependencies(self) -> None:
        body = self.client.get("/health").json()

        self.assertEqual(body["dependencies"]["database"], "not_configured")
        self.assertEqual(body["dependencies"]["llm"], "not_configured")

    def test_health_reports_configured_dependencies(self) -> None:
        settings = BackendSettings(
            database_url="postgresql://localhost/learnguard",
            llm_api_key="test-key",
        )
        body = TestClient(create_app(settings)).get("/health").json()

        # A database_url being set means the health check attempts a real
        # ping: it reports reachable/unreachable, never not_configured.
        self.assertIn(
            body["dependencies"]["database"], ("reachable", "unreachable")
        )
        self.assertEqual(body["dependencies"]["llm"], "configured")


class ErrorContractTests(unittest.TestCase):
    def test_unknown_route_returns_json_detail(self) -> None:
        client = TestClient(create_app(BackendSettings()))

        response = client.get("/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
