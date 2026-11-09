"""/evaluations endpoints: read-only scorecards published by ml-data.

Uses a temporary EVALUATIONS_DIR so no database and no real artifact
directory are needed — the suite stays green in CI.
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from learnguard_backend.api import eval_runner
from learnguard_backend.api.app import _is_valid_run_id, create_app
from learnguard_backend.settings import BackendSettings


def _write_scorecard(run_dir: Path, filename: str, subject: str,
                     fail: int = 0, warn: int = 0, total: int = 2) -> None:
    """Write a minimal eval-report.v1 scorecard for one subject."""
    passed = total - fail - warn
    checks = (
        [{"id": f"c{i}", "name": f"check {i}", "status": "fail"}
         for i in range(fail)]
        + [{"id": f"w{i}", "name": f"check {i}", "status": "warn"}
           for i in range(warn)]
        + [{"id": f"p{i}", "name": f"check {i}", "status": "pass"}
           for i in range(passed)]
    )
    card = {
        "schema_version": "eval-report.v1",
        "run_id": run_dir.name,
        "subject": subject,
        "created_at": "2026-11-05T18:06:10+00:00",
        "inputs": {},
        "checks": checks,
        "summary": {
            "total": total, "pass": passed, "warn": warn, "fail": fail,
            "skipped": 0,
        },
    }
    (run_dir / filename).write_text(
        json.dumps(card), encoding="utf-8"
    )


def _write_run(root: Path, run_id: str, subjects: dict[str, int] | None = None
               ) -> Path:
    """Write a full run dir (manifest + scorecards) into ``root``."""
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    subjects = subjects if subjects is not None else {"data": 0}
    for subject, fail_count in subjects.items():
        _write_scorecard(run_dir, f"eval_{subject}.json", subject,
                         fail=fail_count)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "eval-report.v1",
                "run_id": run_id,
                "created_at": "2026-11-05T18:06:11.849425+00:00",
                "subjects": list(subjects),
            }
        ),
        encoding="utf-8",
    )
    return run_dir


class EvaluationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "evaluations"
        self.root.mkdir()
        self.client = TestClient(
            create_app(BackendSettings(evaluations_dir=str(self.root)))
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_returns_runs_newest_first(self) -> None:
        _write_run(self.root, "eval-report-20261105", subjects={"data": 0})
        older = self.root / "eval-report-20261101"
        _write_run(self.root, "eval-report-20261101", subjects={"ml": 0})
        # Rewrite the older run's manifest with an earlier created_at.
        manifest = json.loads((older / "manifest.json").read_text())
        manifest["created_at"] = "2026-11-01T10:00:00+00:00"
        (older / "manifest.json").write_text(json.dumps(manifest))

        response = self.client.get("/evaluations")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 20)
        self.assertEqual(
            [run["run_id"] for run in body["runs"]],
            ["eval-report-20261105", "eval-report-20261101"],
        )
        first = body["runs"][0]
        self.assertEqual(first["subjects"], ["data"])
        self.assertEqual(
            first["reports_available"], ["eval_data.json"]
        )
        self.assertEqual(
            first["combined_summary"],
            {"total": 2, "pass": 2, "warn": 0, "fail": 0, "skipped": 0},
        )
        self.assertEqual(first["overall_status"], "pass")

    def test_list_overall_status_reflects_checks(self) -> None:
        _write_run(self.root, "run-warn", subjects={"data": 0})
        _write_run(self.root, "run-fail", subjects={"data": 0})
        # Inject one warn and one fail check.
        warn_dir = self.root / "run-warn" / "eval_data.json"
        card = json.loads(warn_dir.read_text())
        card["summary"] = {"total": 2, "pass": 1, "warn": 1, "fail": 0,
                           "skipped": 0}
        warn_dir.write_text(json.dumps(card))
        fail_dir = self.root / "run-fail" / "eval_data.json"
        card = json.loads(fail_dir.read_text())
        card["summary"] = {"total": 2, "pass": 0, "warn": 1, "fail": 1,
                           "skipped": 0}
        fail_dir.write_text(json.dumps(card))

        by_id = {
            run["run_id"]: run
            for run in self.client.get("/evaluations").json()["runs"]
        }
        self.assertEqual(by_id["run-warn"]["overall_status"], "warn")
        self.assertEqual(by_id["run-fail"]["overall_status"], "fail")

    def test_detail_returns_scorecards_keyed_by_subject(self) -> None:
        _write_run(
            self.root,
            "eval-report-20261106",
            subjects={"data": 0, "ml": 1},
        )
        response = self.client.get("/evaluations/eval-report-20261106")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], "eval-report-20261106")
        self.assertEqual(
            body["created_at"], "2026-11-05T18:06:11.849425+00:00"
        )
        self.assertEqual(sorted(body["reports"]), ["data", "ml"])
        # The scorecard is returned intact, not reshaped.
        data_card = body["reports"]["data"]
        self.assertEqual(data_card["schema_version"], "eval-report.v1")
        self.assertEqual(data_card["subject"], "data")
        self.assertEqual(len(data_card["checks"]), 2)
        self.assertEqual(data_card["summary"]["fail"], 0)
        self.assertEqual(body["reports"]["ml"]["summary"]["fail"], 1)

    def test_detail_unknown_run_is_404(self) -> None:
        response = self.client.get("/evaluations/eval-nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "evaluation_not_found")

    def test_detail_leading_dot_run_id_is_400(self) -> None:
        # ".." routes to a single path segment but must be rejected.
        response = self.client.get("/evaluations/%2E%2E")
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid_run_id", response.json()["detail"])

    def test_path_traversal_never_reaches_the_filesystem(self) -> None:
        # ASGI decodes %2F before routing, so "../secret" never even
        # reaches the handler (router 404); and the validation layer
        # rejects any traversal-shaped id that does route. Either way it
        # can never escape EVALUATIONS_DIR.
        self.assertFalse(_is_valid_run_id("../secret"))
        self.assertFalse(_is_valid_run_id(".."))
        self.assertFalse(_is_valid_run_id(".hidden"))
        response = self.client.get("/evaluations/%2E%2E%2Fsecret")
        self.assertIn(response.status_code, (400, 404))
        self.assertNotEqual(response.status_code, 200)

    def test_endpoints_503_when_dir_not_configured(self) -> None:
        client = TestClient(create_app(BackendSettings()))
        for path in ("/evaluations", "/evaluations/eval-report-20261106"):
            response = client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json()["detail"],
                "evaluations_dir_not_configured (set EVALUATIONS_DIR)",
            )

    def test_broken_subdir_is_skipped(self) -> None:
        _write_run(self.root, "eval-good", subjects={"data": 0})
        broken = self.root / "eval-broken"
        broken.mkdir()  # no manifest.json at all
        (broken / "eval_data.json").write_text(
            json.dumps({"subject": "data"}), encoding="utf-8"
        )

        body = self.client.get("/evaluations").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["runs"][0]["run_id"], "eval-good")
        detail = self.client.get("/evaluations/eval-broken")
        self.assertEqual(detail.status_code, 404)

    def test_corrupt_manifest_is_skipped(self) -> None:
        _write_run(self.root, "eval-good", subjects={"data": 0})
        corrupt = self.root / "eval-corrupt"
        corrupt.mkdir()
        (corrupt / "manifest.json").write_text("{not json", encoding="utf-8")
        wrong_schema = self.root / "eval-wrong-schema"
        wrong_schema.mkdir()
        (wrong_schema / "manifest.json").write_text(
            json.dumps({"schema_version": "other.v9", "run_id": "x"}),
            encoding="utf-8",
        )

        body = self.client.get("/evaluations").json()
        self.assertEqual(
            [run["run_id"] for run in body["runs"]], ["eval-good"]
        )

    def test_limit_is_respected(self) -> None:
        for i in range(3):
            _write_run(self.root, f"eval-run-{i}", subjects={"data": 0})
        body = self.client.get("/evaluations", params={"limit": 2}).json()
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["runs"]), 2)


class EvaluationRunTriggerTests(unittest.TestCase):
    """POST /evaluations/run + GET /evaluations/run-status.

    The module-level default subprocess runner is patched out for every
    test in this class: no real ml-data CLI is ever spawned here.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "evaluations"
        self.root.mkdir()
        self.ml_repo = Path(self._tmp.name) / "ml-repo"
        self.ml_repo.mkdir()
        eval_runner.reset_state()
        self._original_default_runner = eval_runner._subprocess_runner
        eval_runner._subprocess_runner = self._fake_subprocess_runner
        self._gate: threading.Event | None = None
        self._publish = True
        self._returncode = 0
        self._raise: Exception | None = None
        self.client = TestClient(
            create_app(
                BackendSettings(
                    evaluations_dir=str(self.root),
                    ml_repo_dir=str(self.ml_repo),
                )
            )
        )

    def tearDown(self) -> None:
        eval_runner._subprocess_runner = self._original_default_runner
        eval_runner.reset_state()
        self._tmp.cleanup()

    def _fake_subprocess_runner(
        self, config: dict, run_id: str
    ) -> int:
        """Same signature as eval_runner._subprocess_runner (patched in)."""
        if self._raise is not None:
            raise self._raise
        if self._gate is not None:
            self._gate.wait(timeout=10)
        if self._publish:
            _write_run(
                Path(config["evaluations_dir"]), run_id, subjects={"data": 0}
            )
        return self._returncode

    def _wait_for_status(self, *statuses: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        body: dict = {}
        while time.monotonic() < deadline:
            body = self.client.get("/evaluations/run-status").json()
            if body["status"] in statuses:
                return body
            time.sleep(0.05)
        self.fail(f"status never reached {statuses}: {body}")

    def test_run_status_idle_initially(self) -> None:
        response = self.client.get("/evaluations/run-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "idle",
                "run_id": None,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "summary": None,
            },
        )

    def test_run_happy_path_started_then_done_with_summary(self) -> None:
        response = self.client.post("/evaluations/run")
        self.assertEqual(response.status_code, 202)
        started = response.json()
        self.assertEqual(started["status"], "started")
        self.assertTrue(started["run_id"].startswith("eval-ui-"))
        body = self._wait_for_status("done")
        self.assertEqual(body["run_id"], started["run_id"])
        self.assertIsNone(body["error"])
        self.assertEqual(
            body["summary"],
            {"total": 2, "pass": 2, "warn": 0, "fail": 0, "skipped": 0},
        )
        self.assertIsNotNone(body["started_at"])
        self.assertIsNotNone(body["finished_at"])

    def test_second_start_while_running_is_409(self) -> None:
        self._gate = threading.Event()
        try:
            first = self.client.post("/evaluations/run")
            self.assertEqual(first.status_code, 202)
            second = self.client.post("/evaluations/run")
            self.assertEqual(second.status_code, 409)
            self.assertEqual(
                second.json()["detail"], "evaluation_already_running"
            )
        finally:
            self._gate.set()
        self._wait_for_status("done")

    def test_run_status_reports_running_then_done(self) -> None:
        self._gate = threading.Event()
        try:
            self.client.post("/evaluations/run")
            body = self.client.get("/evaluations/run-status").json()
            self.assertEqual(body["status"], "running")
            self.assertTrue(body["run_id"].startswith("eval-ui-"))
        finally:
            self._gate.set()
        self._wait_for_status("done")

    def test_run_503_when_ml_repo_dir_not_configured(self) -> None:
        client = TestClient(
            create_app(BackendSettings(evaluations_dir=str(self.root)))
        )
        response = client.post("/evaluations/run")
        self.assertEqual(response.status_code, 503)
        self.assertIn("ml_runner_not_configured", response.json()["detail"])

    def test_run_503_when_evaluations_dir_not_configured(self) -> None:
        client = TestClient(create_app(BackendSettings(ml_repo_dir="x")))
        response = client.post("/evaluations/run")
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "evaluations_dir_not_configured", response.json()["detail"]
        )

    def test_nonzero_exit_sets_error_state(self) -> None:
        self._returncode = 1
        self._publish = False
        self.client.post("/evaluations/run")
        body = self._wait_for_status("error")
        self.assertIn("exited with code 1", body["error"])
        self.assertIsNone(body["summary"])

    def test_runner_exception_sets_error_state(self) -> None:
        self._raise = RuntimeError("subprocess exploded")
        self._publish = False
        self.client.post("/evaluations/run")
        body = self._wait_for_status("error")
        self.assertIn("subprocess exploded", body["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
