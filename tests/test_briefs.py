"""M8 advisor brief tests: service unit tests + API integration tests.

The LLM is always stubbed (FakeLLM) — no network calls in the test suite.
API tests use the shared demo PostgreSQL (learnguard-pg) when reachable and
clean up every brief/decision/llm_call/audit row they create.
"""

import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import text

from learnguard_backend.api.app import create_app
from learnguard_backend.db.models import MLPredictionRecord
from learnguard_backend.db.session import create_session_factory
from learnguard_backend.services.briefs import (
    STATUS_FALLBACK_REVIEW,
    STATUS_GENERATED,
    STATUS_UNAVAILABLE,
    AdvisorBriefDraft,
    AdvisorBriefService,
    BriefEvidence,
    BriefReason,
    build_context,
)
from learnguard_backend.services.llm import LLMResponse, LLMUsage
from learnguard_backend.settings import BackendSettings

TEST_DATABASE_URL = "postgresql://learnguard:learnguard@localhost:5432/learnguard"

FAKE_MODEL = "gemini/gemini-3.1-flash-lite"

VALID_CONTENT = json.dumps(
    {
        "summary": "Sinh viên có nguy cơ giỏi nhưng hoạt động gần đây giảm.",
        "reasons": [
            {
                "text": "Số lượt click trước cutoff thấp so với mặt bằng.",
                "evidence_features": ["clicks__n_clicks_before_cutoff"],
            },
            {
                "text": "Điểm bài tập trung bình ở mức khá.",
                "evidence_features": ["avg_score_submitted"],
            },
        ],
        "support_suggestions": ["support_tutoring", "support_extension"],
        "limitations": ["Dữ liệu chỉ tới cutoff day 27."],
    },
    ensure_ascii=False,
)


class FakeLLM:
    """Stub of LLMService.complete — records calls, never touches network."""

    model_name = FAKE_MODEL

    def __init__(self, content: str = VALID_CONTENT, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list[dict] = []

    def complete(self, messages, **options):
        self.calls.append({"messages": list(messages), "options": options})
        if self.error is not None:
            raise self.error
        return LLMResponse(
            content=self.content,
            model=FAKE_MODEL,
            usage=LLMUsage(prompt_tokens=120, completion_tokens=200, total_tokens=320),
            cost_usd=0.000123,
            latency_ms=456,
            finish_reason="stop",
        )


def _sample_context() -> dict:
    return {
        "student_key": "c4c2e78b683c603a",
        "status": "scored",
        "risk_score": 0.96,
        "threshold": 0.5,
        "uncertainty": 0.08,
        "cutoff_day": 27,
        "top_signals": [
            {
                "feature_name": "clicks__n_clicks_before_cutoff",
                "contribution": -0.25,
                "direction": "decreases_risk",
                "observed_value": 314.0,
            }
        ],
        "features": {
            "n_clicks_before_cutoff": 314.0,
            "avg_score_submitted": 73.0,
        },
    }


class AdvisorBriefServiceTests(unittest.TestCase):
    def test_generate_happy_path(self) -> None:
        fake = FakeLLM(VALID_CONTENT)
        result = AdvisorBriefService(llm=fake).generate("GGG/2014B/abc", _sample_context())

        self.assertEqual(result.status, STATUS_GENERATED)
        self.assertEqual(result.model_name, FAKE_MODEL)
        self.assertEqual(result.latency_ms, 456)
        self.assertEqual(result.cost_usd, 0.000123)
        self.assertEqual(result.prompt_tokens, 120)
        self.assertEqual(result.completion_tokens, 200)
        self.assertIsNone(result.error_reason)
        assert result.draft is not None
        self.assertEqual(result.draft.summary, "Sinh viên có nguy cơ giỏi nhưng hoạt động gần đây giảm.")
        self.assertEqual(
            result.draft.reasons[0].evidence_features,
            ["clicks__n_clicks_before_cutoff"],
        )
        self.assertEqual(
            result.draft.support_template_ids,
            ["support_tutoring", "support_extension"],
        )

    def test_generate_sends_system_prompt_and_no_pii(self) -> None:
        fake = FakeLLM(VALID_CONTENT)
        AdvisorBriefService(llm=fake).generate("x", _sample_context())

        self.assertEqual(len(fake.calls), 1)
        messages = fake.calls[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("CỐ VẤN", messages[0]["content"])
        user_payload = messages[1]["content"]
        self.assertIn("student_key", user_payload)
        # No direct identifier ever leaves the backend.
        self.assertNotIn("id_student", user_payload)
        self.assertNotIn("email", user_payload)
        self.assertNotIn("full_name", user_payload)

    def test_generate_removes_ungrounded_citations(self) -> None:
        content = json.dumps(
            {
                "summary": "Tóm tắt.",
                "reasons": [
                    {
                        "text": "Reason bịa feature.",
                        "evidence_features": ["made_up_feature"],
                    },
                    {
                        "text": "Reason hợp lệ.",
                        "evidence_features": ["avg_score_submitted"],
                    },
                ],
                "support_suggestions": [],
                "limitations": [],
            },
            ensure_ascii=False,
        )
        result = AdvisorBriefService(llm=FakeLLM(content)).generate("x", _sample_context())

        self.assertEqual(result.status, STATUS_GENERATED)
        assert result.draft is not None
        self.assertEqual(
            [r.text for r in result.draft.reasons], ["Reason hợp lệ."]
        )
        self.assertTrue(
            any("made_up_feature" in item for item in result.draft.limitations)
        )

    def test_validate_citations_reports_unknown_features(self) -> None:
        draft = AdvisorBriefDraft(
            summary="s",
            reasons=[
                BriefReason(text="a", evidence_features=["f1", "ghost"]),
                BriefReason(text="b", evidence_features=["ghost2"]),
            ],
            evidence=[],
            support_template_ids=[],
        )
        violations = AdvisorBriefService().validate_citations(
            draft, [BriefEvidence("f1", 1.0), BriefEvidence("f2", 2.0)]
        )
        self.assertEqual(violations, ["ghost", "ghost2"])

    def test_llm_failure_returns_fallback_review_not_exception(self) -> None:
        service = AdvisorBriefService(llm=FakeLLM(error=RuntimeError("provider down")))
        result = service.generate("x", _sample_context())

        self.assertEqual(result.status, STATUS_FALLBACK_REVIEW)
        self.assertIn("llm_error", result.error_reason or "")
        assert result.draft is not None
        self.assertTrue(result.draft.summary)
        self.assertEqual(result.draft.reasons, [])
        self.assertTrue(result.draft.limitations)

    def test_unparseable_response_returns_fallback_review(self) -> None:
        result = AdvisorBriefService(llm=FakeLLM("Xin lỗi, tôi không thể trả lời.")).generate(
            "x", _sample_context()
        )
        self.assertEqual(result.status, STATUS_FALLBACK_REVIEW)
        self.assertEqual(result.error_reason, "llm_response_not_parseable")

    def test_make_fallback_unavailable_without_fabrication(self) -> None:
        result = AdvisorBriefService().make_fallback(
            "x", "llm_not_configured", status=STATUS_UNAVAILABLE
        )
        self.assertEqual(result.status, STATUS_UNAVAILABLE)
        self.assertEqual(result.error_reason, "llm_not_configured")
        assert result.draft is not None
        self.assertEqual(result.draft.reasons, [])
        self.assertEqual(result.draft.evidence, [])
        self.assertTrue(any("llm_not_configured" in l for l in result.draft.limitations))

    def test_generate_without_context_falls_back(self) -> None:
        result = AdvisorBriefService(llm=FakeLLM()).generate("x", None)
        self.assertEqual(result.status, STATUS_FALLBACK_REVIEW)
        self.assertEqual(result.error_reason, "missing_prediction_context")


class BuildContextTests(unittest.TestCase):
    def test_context_maps_signal_values_and_excludes_pii(self) -> None:
        record = MLPredictionRecord(
            code_module="GGG",
            code_presentation="2014B",
            student_key="c4c2e78b683c603a",
            split="test",
            risk_score=0.96,
            predicted_label=1,
            threshold=0.5,
            uncertainty=0.08,
            status="scored",
            review_reason=None,
            model_version="m1",
            snapshot_version="s1",
            cutoff_day=27,
            top_signals_json=json.dumps(
                [
                    {
                        "feature_name": "score__missingindicator_avg_score_submitted",
                        "contribution": -0.29,
                        "direction": "decreases_risk",
                    }
                ]
            ),
            features_json=json.dumps({"avg_score_submitted": 73.0}),
            run_id="r1",
        )
        context = build_context(record)
        self.assertEqual(context["student_key"], "c4c2e78b683c603a")
        self.assertEqual(context["risk_score"], 0.96)
        self.assertEqual(
            context["top_signals"][0]["observed_value"], 73.0
        )
        self.assertNotIn("id_student", json.dumps(context))

    def test_context_survives_corrupt_json(self) -> None:
        record = MLPredictionRecord(
            code_module="GGG",
            code_presentation="2014B",
            student_key="c4c2e78b683c603a",
            split="test",
            risk_score=None,
            predicted_label=None,
            threshold=0.5,
            uncertainty=None,
            status="insufficient_data",
            review_reason=None,
            model_version="m1",
            snapshot_version="s1",
            cutoff_day=27,
            top_signals_json="{not json",
            features_json=None,
            run_id="r1",
        )
        context = build_context(record)
        self.assertEqual(context["top_signals"], [])
        self.assertEqual(context["features"], {})


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
class BriefEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeLLM(VALID_CONTENT)
        cls.client = TestClient(create_app(SETTINGS, llm_service=cls.fake))
        cls.factory = create_session_factory(TEST_DATABASE_URL)
        page = cls.client.get(
            "/predictions", params={"status": "scored", "limit": 1}
        ).json()
        item = page["items"][0]
        cls.pred_keys = (
            item["code_module"],
            item["code_presentation"],
            item["student_key"],
        )

    def setUp(self) -> None:
        self.fake.content = VALID_CONTENT
        self.fake.error = None
        self.fake.calls = []

    def _generate(self, **overrides):
        body = dict(zip(("code_module", "code_presentation", "student_key"), self.pred_keys))
        body.update(overrides)
        return self.client.post("/briefs/generate", json=body)

    def _cleanup_brief(self, brief_id: int) -> None:
        with self.factory() as session:
            session.execute(
                text("DELETE FROM decisions WHERE brief_id = :i"), {"i": brief_id}
            )
            session.execute(
                text("DELETE FROM llm_calls WHERE brief_id = :i"), {"i": brief_id}
            )
            session.execute(
                text(
                    "DELETE FROM audit_logs WHERE entity = 'brief' "
                    "AND entity_id = :e"
                ),
                {"e": str(brief_id)},
            )
            session.execute(
                text("DELETE FROM briefs WHERE brief_id = :i"), {"i": brief_id}
            )
            session.commit()

    # -- generate ---------------------------------------------------------

    def test_generate_happy_path_and_persistence(self) -> None:
        response = self._generate()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.addCleanup(self._cleanup_brief, body["brief_id"])

        self.assertIsInstance(body["brief_id"], int)
        self.assertEqual(body["status"], "generated")
        self.assertEqual(
            (body["code_module"], body["code_presentation"], body["student_key"]),
            self.pred_keys,
        )
        self.assertGreaterEqual(body["version"], 1)
        self.assertTrue(body["summary"])
        self.assertEqual(len(body["reasons"]), 2)
        for reason in body["reasons"]:
            self.assertIn("text", reason)
            self.assertIn("evidence_features", reason)
        self.assertEqual(
            body["support_suggestions"], ["support_tutoring", "support_extension"]
        )
        self.assertEqual(body["model_name"], FAKE_MODEL)
        self.assertEqual(body["latency_ms"], 456)
        self.assertTrue(body["created_at"])

        # briefs row
        with self.factory() as session:
            row = session.execute(
                text(
                    "SELECT content, citations, status FROM briefs "
                    "WHERE brief_id = :i"
                ),
                {"i": body["brief_id"]},
            ).one()
        self.assertEqual(row.content["generation_status"], "generated")
        self.assertEqual(row.status, "awaiting_review")
        self.assertEqual(len(row.citations), 2)  # only cited evidence stored

        # llm_calls row with usage
        with self.factory() as session:
            call = session.execute(
                text(
                    "SELECT model, prompt_tokens, completion_tokens, latency_ms, "
                    "cost_usd, status, brief_id FROM llm_calls "
                    "WHERE brief_id = :i ORDER BY call_id DESC LIMIT 1"
                ),
                {"i": body["brief_id"]},
            ).one()
        self.assertEqual(call.model, FAKE_MODEL)
        self.assertEqual(call.prompt_tokens, 120)
        self.assertEqual(call.completion_tokens, 200)
        self.assertEqual(call.latency_ms, 456)
        self.assertAlmostEqual(float(call.cost_usd), 0.000123, places=6)
        self.assertEqual(call.status, "ok")

        # audit_logs row
        with self.factory() as session:
            log = session.execute(
                text(
                    "SELECT action, entity, user_id FROM audit_logs "
                    "WHERE entity = 'brief' AND entity_id = :e"
                ),
                {"e": str(body["brief_id"])},
            ).one()
        self.assertEqual(log.action, "brief_generate")
        self.assertIsNone(log.user_id)

    def test_generate_404_for_unknown_prediction(self) -> None:
        response = self._generate(
            code_module="ZZZ", code_presentation="2099Z", student_key="0000000000000000"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "prediction_not_found")

    def test_regenerate_bumps_version_on_same_brief_id(self) -> None:
        first = self._generate().json()
        self.addCleanup(self._cleanup_brief, first["brief_id"])
        second = self._generate().json()
        self.addCleanup(self._cleanup_brief, second["brief_id"])

        self.assertEqual(second["brief_id"], first["brief_id"])
        self.assertEqual(second["version"], first["version"] + 1)

    def test_generate_fallback_when_llm_fails(self) -> None:
        self.fake.error = RuntimeError("provider down")
        response = self._generate()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.addCleanup(self._cleanup_brief, body["brief_id"])

        self.assertEqual(body["status"], "fallback_review")
        self.assertTrue(body["summary"])
        self.assertEqual(body["reasons"], [])
        self.assertTrue(body["limitations"])

        with self.factory() as session:
            call = session.execute(
                text(
                    "SELECT status FROM llm_calls WHERE brief_id = :i "
                    "ORDER BY call_id DESC LIMIT 1"
                ),
                {"i": body["brief_id"]},
            ).one()
        self.assertEqual(call.status, "api_error")

    # -- get ---------------------------------------------------------------

    def test_get_brief_roundtrip(self) -> None:
        created = self._generate().json()
        self.addCleanup(self._cleanup_brief, created["brief_id"])

        fetched = self.client.get(f"/briefs/{created['brief_id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["brief_id"], created["brief_id"])
        self.assertEqual(fetched.json()["summary"], created["summary"])

    def test_get_unknown_brief_404(self) -> None:
        response = self.client.get("/briefs/999999999")
        self.assertEqual(response.status_code, 404)

    # -- decision ------------------------------------------------------------

    def test_decision_accepted_persists(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        response = self.client.post(
            f"/briefs/{brief['brief_id']}/decision", json={"decision": "accepted"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["brief_id"], brief["brief_id"])
        self.assertEqual(body["decision"], "accepted")
        self.assertTrue(body["decided_at"])

        with self.factory() as session:
            status = session.execute(
                text("SELECT status FROM briefs WHERE brief_id = :i"),
                {"i": brief["brief_id"]},
            ).scalar_one()
            decision = session.execute(
                text(
                    "SELECT action FROM decisions WHERE brief_id = :i "
                    "ORDER BY decision_id DESC LIMIT 1"
                ),
                {"i": brief["brief_id"]},
            ).one()
        self.assertEqual(status, "accepted")
        self.assertEqual(decision.action, "accept")

    def test_decision_rejected_then_new_brief_version(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        rejected = self.client.post(
            f"/briefs/{brief['brief_id']}/decision", json={"decision": "rejected"}
        )
        self.assertEqual(rejected.status_code, 200)
        with self.factory() as session:
            status = session.execute(
                text("SELECT status FROM briefs WHERE brief_id = :i"),
                {"i": brief["brief_id"]},
            ).scalar_one()
        self.assertEqual(status, "rejected")

        # After a rejection, regeneration starts a NEW brief row, version+1.
        regenerated = self._generate().json()
        self.addCleanup(self._cleanup_brief, regenerated["brief_id"])
        self.assertNotEqual(regenerated["brief_id"], brief["brief_id"])
        self.assertEqual(regenerated["version"], brief["version"] + 1)

    def test_decision_invalid_400(self) -> None:
        response = self.client.post(
            "/briefs/1/decision", json={"decision": "maybe"}
        )
        self.assertEqual(response.status_code, 400)

    def test_decision_unknown_brief_404(self) -> None:
        response = self.client.post(
            "/briefs/999999999/decision", json={"decision": "accepted"}
        )
        self.assertEqual(response.status_code, 404)

    # -- edit ---------------------------------------------------------------

    def test_edit_summary_and_suggestions_persists(self) -> None:
        created = self._generate().json()
        brief_id = created["brief_id"]
        self.addCleanup(self._cleanup_brief, brief_id)

        response = self.client.put(
            f"/briefs/{brief_id}",
            json={
                "summary": "Đã chỉnh bởi advisor.",
                "support_suggestions": ["support_mentoring"],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        # Contract: same shape, edited content, no version bump.
        self.assertEqual(body["brief_id"], brief_id)
        self.assertEqual(body["summary"], "Đã chỉnh bởi advisor.")
        self.assertEqual(body["support_suggestions"], ["support_mentoring"])
        self.assertEqual(body["reasons"], created["reasons"])  # untouched
        self.assertEqual(body["limitations"], created["limitations"])
        self.assertEqual(body["version"], created["version"])  # edit ≠ bump
        self.assertEqual(body["status"], created["status"])  # status kept
        self.assertTrue(body["edited_at"])

        # DB: content updated in place, citations column untouched.
        with self.factory() as session:
            row = session.execute(
                text(
                    "SELECT content, citations, status, version FROM briefs "
                    "WHERE brief_id = :i"
                ),
                {"i": brief_id},
            ).one()
            log = session.execute(
                text(
                    "SELECT action, user_id FROM audit_logs "
                    "WHERE entity = 'brief' AND entity_id = :e "
                    "AND action = 'brief_edit'"
                ),
                {"e": str(brief_id)},
            ).one()
        self.assertEqual(row.content["summary"], "Đã chỉnh bởi advisor.")
        self.assertTrue(row.content["edited_at"])
        self.assertEqual(row.content["edited_by"], "system@learnguard.local")
        self.assertIn("advisor_edit", row.content["source_history"])
        self.assertEqual(row.version, created["version"])
        self.assertEqual(row.status, "awaiting_review")  # DB status kept
        self.assertEqual(body["status"], created["status"])
        self.assertEqual(len(row.citations), 2)  # evidence column untouched

        # Audit log with the system principal (M6 auth placeholder).
        self.assertEqual(log.action, "brief_edit")
        self.assertIsNotNone(log.user_id)

    def test_edit_empty_body_400(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        response = self.client.put(f"/briefs/{brief['brief_id']}", json={})
        self.assertEqual(response.status_code, 400)

    def test_edit_identity_fields_only_400(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        response = self.client.put(
            f"/briefs/{brief['brief_id']}",
            json={"brief_id": 42, "student_key": "hacked", "code_module": "XXX"},
        )
        self.assertEqual(response.status_code, 400)

    def test_edit_whitespace_summary_400(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        response = self.client.put(
            f"/briefs/{brief['brief_id']}", json={"summary": "   "}
        )
        self.assertEqual(response.status_code, 400)

    def test_edit_bad_reasons_shape_400(self) -> None:
        brief = self._generate().json()
        self.addCleanup(self._cleanup_brief, brief["brief_id"])

        for bad in (["not a dict"], [{"missing_text": True}], "nope"):
            response = self.client.put(
                f"/briefs/{brief['brief_id']}", json={"reasons": bad}
            )
            self.assertEqual(response.status_code, 400, f"payload: {bad!r}")

    def test_edit_unknown_brief_404(self) -> None:
        response = self.client.put(
            "/briefs/999999999", json={"summary": "Sửa brief lạ."}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "brief_not_found")

    def test_get_edited_at_null_before_edit_then_set_after(self) -> None:
        created = self._generate().json()
        brief_id = created["brief_id"]
        self.addCleanup(self._cleanup_brief, brief_id)

        fetched = self.client.get(f"/briefs/{brief_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertIsNone(fetched.json()["edited_at"])

        edited = self.client.put(
            f"/briefs/{brief_id}", json={"summary": "Bản đã sửa."}
        )
        self.assertEqual(edited.status_code, 200)
        self.assertTrue(edited.json()["edited_at"])

        refetched = self.client.get(f"/briefs/{brief_id}")
        self.assertEqual(refetched.status_code, 200)
        self.assertTrue(refetched.json()["edited_at"])
        self.assertEqual(refetched.json()["summary"], "Bản đã sửa.")

    def test_regenerate_after_edit_resets_edited_at_and_bumps_version(self) -> None:
        created = self._generate().json()
        brief_id = created["brief_id"]
        self.addCleanup(self._cleanup_brief, brief_id)

        edited = self.client.put(
            f"/briefs/{brief_id}", json={"summary": "Bản đã sửa."}
        )
        self.assertEqual(edited.status_code, 200)

        regenerated = self._generate().json()
        self.addCleanup(self._cleanup_brief, regenerated["brief_id"])
        self.assertEqual(regenerated["brief_id"], brief_id)
        self.assertEqual(regenerated["version"], created["version"] + 1)
        self.assertIsNone(regenerated["edited_at"])  # fresh LLM content
        self.assertEqual(regenerated["summary"], created["summary"])

        with self.factory() as session:
            row = session.execute(
                text("SELECT content FROM briefs WHERE brief_id = :i"),
                {"i": brief_id},
            ).one()
        self.assertIsNone(row.content["edited_at"])
        self.assertEqual(row.content["source_history"], [])


if __name__ == "__main__":
    unittest.main()
