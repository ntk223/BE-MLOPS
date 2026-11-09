"""Advisor API integration tests against the demo PostgreSQL.

Skipped when the database is unreachable so the suite stays green.
"""

import csv
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from learnguard_backend.api.app import create_app
from learnguard_backend.db.session import create_session_factory
from learnguard_backend.services.student_keys import pseudonymous_key
from learnguard_backend.settings import BackendSettings

TEST_DATABASE_URL = "postgresql://learnguard:learnguard@localhost:5432/learnguard"

# Hardcoded test-only path to the raw OULAD CSV dataset (never shipped as
# a backend setting); tests are skipped when it is absent.
OULAD_TEST_DATA_DIR = "/home/ntkien/MLOPS/ml-data/data/oulad/archive"


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


@unittest.skipUnless(DB_AVAILABLE, "demo PostgreSQL not reachable")
class PredictionStatsEndpointTests(unittest.TestCase):
    """GET /predictions/stats: dashboard stat cards against the demo DB."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app(SETTINGS))

    def test_stats_shape_and_demo_numbers(self) -> None:
        """Contract shape + the real demo-batch distribution (300 rows)."""
        response = self.client.get("/predictions/stats")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "flagged_total",
                "scored",
                "insufficient_data",
                "manual_review",
                "no_prediction",
                "above_threshold",
            },
        )
        self.assertEqual(body["flagged_total"], 300)
        self.assertEqual(body["scored"], 250)
        self.assertEqual(body["insufficient_data"], 10)
        self.assertEqual(body["manual_review"], 40)
        self.assertEqual(body["no_prediction"], 0)
        self.assertEqual(body["above_threshold"], 129)

    def test_status_filter_narrows_flagged_total_only(self) -> None:
        """flagged_total is after filters; the 4 card numbers do not move."""
        body = self.client.get(
            "/predictions/stats", params={"status": "scored"}
        ).json()
        self.assertEqual(body["flagged_total"], 250)
        # Cards still show the full distribution (frontend renders 4 cards).
        self.assertEqual(body["scored"], 250)
        self.assertEqual(body["insufficient_data"], 10)
        self.assertEqual(body["manual_review"], 40)
        self.assertEqual(body["no_prediction"], 0)

    def test_code_module_filter_returns_smaller_subset(self) -> None:
        unfiltered = self.client.get("/predictions/stats").json()
        filtered = self.client.get(
            "/predictions/stats", params={"code_module": "BBB"}
        ).json()
        self.assertLess(filtered["flagged_total"], unfiltered["flagged_total"])
        self.assertEqual(
            filtered["flagged_total"],
            filtered["scored"]
            + filtered["insufficient_data"]
            + filtered["manual_review"]
            + filtered["no_prediction"],
        )
        self.assertLessEqual(filtered["above_threshold"], filtered["scored"])

    def test_invalid_status_rejected_with_400(self) -> None:
        response = self.client.get(
            "/predictions/stats", params={"status": "hacked"}
        )
        self.assertEqual(response.status_code, 400)

    def test_audit_log_written_for_stats(self) -> None:
        self.client.get("/predictions/stats")
        factory = create_session_factory(TEST_DATABASE_URL)
        with factory() as session:
            row = session.execute(
                text(
                    "SELECT action, entity FROM audit_logs "
                    "ORDER BY at DESC LIMIT 1"
                )
            ).one()
        self.assertEqual(row.action, "prediction_stats")
        self.assertEqual(row.entity, "ml_prediction")


class PredictionEndpointNoDbTests(unittest.TestCase):
    def test_503_without_database_url(self) -> None:
        client = TestClient(create_app(BackendSettings()))
        response = client.get("/predictions")
        self.assertEqual(response.status_code, 503)


class PredictionActivityNoDataTests(unittest.TestCase):
    """GET /predictions/.../activity: 503 oulad_data_not_configured."""

    def test_503_without_oulad_data_dir(self) -> None:
        client = TestClient(create_app(BackendSettings()))
        response = client.get("/predictions/AAA/2013J/abcdef0123456789/activity")
        self.assertEqual(response.status_code, 503)
        self.assertIn("oulad_data_not_configured", response.json()["detail"])

    def test_503_when_oulad_data_dir_missing(self) -> None:
        client = TestClient(
            create_app(BackendSettings(oulad_data_dir="/nonexistent/oulad"))
        )
        response = client.get("/predictions/AAA/2013J/abcdef0123456789/activity")
        self.assertEqual(response.status_code, 503)
        self.assertIn("oulad_data_not_configured", response.json()["detail"])


def _resolve_id_student(
    module: str, presentation: str, student_key: str
) -> int | None:
    """Test-side reverse map (student_key is one-way): brute-force hash."""
    path = Path(OULAD_TEST_DATA_DIR) / "studentInfo.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row["code_module"], row["code_presentation"]) != (
                module,
                presentation,
            ):
                continue
            id_student = int(row["id_student"])
            if pseudonymous_key(module, presentation, id_student) == student_key:
                return id_student
    return None


def _expected_weeks_clicks(module: str, presentation: str, id_student: int) -> list[int]:
    """Independently recompute per-week click totals from studentVle.csv."""
    buckets = [(-14, -1), (0, 6), (7, 13), (14, 20), (21, 27)]
    clicks_by_day: dict[int, int] = {}
    path = Path(OULAD_TEST_DATA_DIR) / "studentVle.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                (row["code_module"], row["code_presentation"])
                != (module, presentation)
                or int(row["id_student"]) != id_student
            ):
                continue
            day = int(row["date"])
            if day < -14 or day > 27:
                continue
            clicks_by_day[day] = clicks_by_day.get(day, 0) + int(row["sum_click"])
    return [
        sum(
            clicks
            for day, clicks in clicks_by_day.items()
            if from_day <= day <= to_day and clicks > 0
        )
        for from_day, to_day in buckets
    ]


def _expected_submissions(
    module: str, presentation: str, id_student: int
) -> list[dict]:
    """Independently recompute the expected submissions list from the raw CSVs."""
    submitted: dict[str, dict[str, str]] = {}
    path = Path(OULAD_TEST_DATA_DIR) / "studentAssessment.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["id_student"]) == id_student:
                submitted[row["id_assessment"]] = row

    expected: list[dict] = []
    path = Path(OULAD_TEST_DATA_DIR) / "assessments.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row["code_module"], row["code_presentation"]) != (
                module,
                presentation,
            ):
                continue
            if not row["date"].strip() or int(row["date"]) > 27:
                continue
            sub = submitted.get(row["id_assessment"])
            expected.append(
                {
                    "assessment_type": row["assessment_type"],
                    "assessment_day": int(row["date"]),
                    "submitted_day": (
                        int(sub["date_submitted"])
                        if sub and sub["date_submitted"].strip()
                        else None
                    ),
                    "score": (
                        int(sub["score"]) if sub and sub["score"].strip() else None
                    ),
                    "weight": (
                        int(row["weight"]) if row["weight"].strip() else None
                    ),
                    "status": "submitted" if sub else "not_submitted",
                }
            )
    expected.sort(key=lambda item: item["assessment_day"])
    return expected


@unittest.skipUnless(
    DB_AVAILABLE and Path(OULAD_TEST_DATA_DIR).is_dir(),
    "demo PostgreSQL or OULAD dataset not available",
)
class PredictionActivityEndpointTests(unittest.TestCase):
    """GET /predictions/{module}/{presentation}/{student_key}/activity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(
            create_app(
                BackendSettings(
                    database_url=TEST_DATABASE_URL,
                    oulad_data_dir=OULAD_TEST_DATA_DIR,
                )
            )
        )
        listed = cls.client.get("/predictions", params={"limit": 1}).json()
        cls.item = listed["items"][0]
        cls.activity_url = "/predictions/{}/{}/{}/activity".format(
            cls.item["code_module"],
            cls.item["code_presentation"],
            cls.item["student_key"],
        )

    def _activity(self) -> dict:
        response = self.client.get(self.activity_url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_shape_and_cutoff_and_five_weeks(self) -> None:
        body = self._activity()
        self.assertEqual(
            set(body),
            {"cutoff_day", "weeks", "submissions", "activity_breakdown"},
        )
        self.assertEqual(body["cutoff_day"], 27)
        self.assertEqual(len(body["weeks"]), 5)
        expected_buckets = [
            ("Trước khóa", -14, -1),
            ("Tuần 1", 0, 6),
            ("Tuần 2", 7, 13),
            ("Tuần 3", 14, 20),
            ("Tuần 4", 21, 27),
        ]
        for week, (label, from_day, to_day) in zip(
            body["weeks"], expected_buckets
        ):
            self.assertEqual(
                (week["label"], week["from_day"], week["to_day"]),
                (label, from_day, to_day),
            )
            self.assertLessEqual(week["days_active"], to_day - from_day + 1)

    def test_weeks_clicks_match_raw_student_vle(self) -> None:
        body = self._activity()
        id_student = _resolve_id_student(
            self.item["code_module"],
            self.item["code_presentation"],
            self.item["student_key"],
        )
        self.assertIsNotNone(id_student)
        self.assertEqual(
            [week["clicks"] for week in body["weeks"]],
            _expected_weeks_clicks(
                self.item["code_module"],
                self.item["code_presentation"],
                id_student,
            ),
        )

    def test_student_with_activity_has_positive_clicks(self) -> None:
        listed = self.client.get("/predictions", params={"limit": 20}).json()
        found = None
        for item in listed["items"]:
            body = self.client.get(
                "/predictions/{}/{}/{}/activity".format(
                    item["code_module"],
                    item["code_presentation"],
                    item["student_key"],
                )
            ).json()
            total = sum(week["clicks"] for week in body["weeks"])
            if total > 0:
                found = total
                break
        self.assertIsNotNone(found)
        self.assertGreater(found, 0)

    def test_submissions_match_raw_csvs(self) -> None:
        body = self._activity()
        id_student = _resolve_id_student(
            self.item["code_module"],
            self.item["code_presentation"],
            self.item["student_key"],
        )
        self.assertIsNotNone(id_student)
        self.assertEqual(
            body["submissions"],
            _expected_submissions(
                self.item["code_module"],
                self.item["code_presentation"],
                id_student,
            ),
        )
        # Both statuses occur across the demo batch (spot-check invariants).
        for row in body["submissions"]:
            self.assertIn(row["status"], {"submitted", "not_submitted"})
            self.assertLessEqual(row["assessment_day"], 27)

    def test_activity_breakdown_sorted_desc_top6(self) -> None:
        body = self._activity()
        breakdown = body["activity_breakdown"]
        self.assertLessEqual(len(breakdown), 6)
        clicks = [row["clicks"] for row in breakdown]
        self.assertEqual(clicks, sorted(clicks, reverse=True))

    def test_unknown_student_key_404(self) -> None:
        response = self.client.get("/predictions/ZZZ/2099Z/deadbeefdeadbeef/activity")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "student_not_found")

    def test_audit_log_written_for_activity(self) -> None:
        self.client.get(self.activity_url)
        factory = create_session_factory(TEST_DATABASE_URL)
        with factory() as session:
            row = session.execute(
                text(
                    "SELECT action, entity, entity_id FROM audit_logs "
                    "ORDER BY at DESC LIMIT 1"
                )
            ).one()
        self.assertEqual(row.action, "prediction_activity")
        self.assertEqual(row.entity, "ml_prediction")
        self.assertEqual(
            row.entity_id,
            "{}/{}/{}".format(
                self.item["code_module"],
                self.item["code_presentation"],
                self.item["student_key"],
            ),
        )


@unittest.skipUnless(DB_AVAILABLE, "demo PostgreSQL not reachable")
class RiskScoreTieredRankingTests(unittest.TestCase):
    """Hàng đợi ưu tiên điểm đáng tin cậy: scored trước, manual_review,
    rồi insufficient_data (điểm ngoại suy trên dữ liệu impute) — nhóm
    thiếu dữ liệu không được chiếm đầu bảng."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app(SETTINGS))

    def test_first_page_excludes_insufficient_data(self) -> None:
        response = self.client.get(
            "/predictions", params={"order_by": "risk_score", "limit": 15}
        )
        self.assertEqual(response.status_code, 200)
        statuses = {item["status"] for item in response.json()["items"]}
        self.assertNotIn("insufficient_data", statuses)

    def test_manual_review_ranks_after_scored(self) -> None:
        response = self.client.get(
            "/predictions",
            params={"order_by": "risk_score", "limit": 100},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        statuses = [item["status"] for item in items]
        if "manual_review" in statuses and "scored" in statuses:
            self.assertLess(
                statuses.index("scored"), statuses.index("manual_review")
            )
        if "insufficient_data" in statuses:
            self.assertEqual(statuses[-10:].count("insufficient_data"), 10)

    def test_other_order_by_untiered(self) -> None:
        """order_by khác (vd scored_at) giữ hành vi cũ — không tier."""
        response = self.client.get(
            "/predictions", params={"order_by": "scored_at", "limit": 50}
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
