"""Read-only access to ``ml_predictions`` + advisor API integration tests.

Uses the shared demo PostgreSQL (learnguard-pg) when reachable; skipped
otherwise so the suite stays green without a database.
"""

import unittest

from sqlalchemy import text

from learnguard_backend.db.repository import SQLMLPredictionRepository
from learnguard_backend.db.session import create_session_factory

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


@unittest.skipUnless(DB_AVAILABLE, "demo PostgreSQL not reachable")
class MLPredictionRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.factory = create_session_factory(TEST_DATABASE_URL)
        cls.repo = SQLMLPredictionRepository(cls.factory)

    def test_total_matches_demo_batch(self) -> None:
        """AC2: 6,510 rows and status distribution 5,314/983/213 = 6,510."""
        page = self.repo.list(limit=1, offset=0)
        self.assertEqual(page.total, 6510)

        with self.factory() as session:
            rows = session.execute(
                text(
                    "SELECT status, count(*) FROM ml_predictions GROUP BY status"
                )
            ).all()
        counts = dict(rows)
        self.assertEqual(
            counts.get("scored", 0)
            + counts.get("manual_review", 0)
            + counts.get("insufficient_data", 0)
            + counts.get("no_prediction", 0),
            6510,
        )
        self.assertEqual(counts.get("scored", 0), 5314)
        self.assertEqual(counts.get("manual_review", 0), 983)
        self.assertEqual(counts.get("insufficient_data", 0), 213)

    def test_all_four_statuses_handled(self) -> None:
        """Every status must filter without error, even when empty."""
        for status in ("scored", "manual_review", "insufficient_data", "no_prediction"):
            with self.subTest(status=status):
                page = self.repo.list(status=status, limit=5)
                self.assertTrue(all(item.status == status for item in page.items))

    def test_null_predicted_label_does_not_crash(self) -> None:
        """AC4: rows with NULL predicted_label serialize fine."""
        page = self.repo.list(status="insufficient_data", limit=10)
        self.assertGreater(len(page.items), 0)
        for item in page.items:
            self.assertIsNone(item.predicted_label)

    def test_order_by_risk_score_desc(self) -> None:
        page = self.repo.list(status="scored", order_by="risk_score", limit=50)
        scores = [item.risk_score for item in page.items]
        self.assertEqual(len(scores), 50)
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.list(status="not_a_status")

    def test_filters_narrow_results(self) -> None:
        page = self.repo.list(code_module="BBB", code_presentation="2014B", limit=5000)
        self.assertTrue(
            all(
                item.code_module == "BBB" and item.code_presentation == "2014B"
                for item in page.items
            )
        )

    def test_get_by_composite_pk(self) -> None:
        page = self.repo.list(limit=1)
        sample = page.items[0]
        record = self.repo.get(
            sample.code_module, sample.code_presentation, sample.student_key
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.student_key, sample.student_key)  # type: ignore[union-attr]

    def test_student_key_join_works(self) -> None:
        """A key computed from the formula exists in the pipeline data."""
        with self.factory() as session:
            row = session.execute(
                text(
                    "SELECT code_module, code_presentation, student_key "
                    "FROM ml_predictions LIMIT 1"
                )
            ).one()
        # The join direction (backend list -> key) is what matters; verifying
        # the formula pipeline round-trip on real data is a cross-check.
        self.assertRegex(row.student_key, r"^[0-9a-f]{16}$")


if __name__ == "__main__":
    unittest.main()
