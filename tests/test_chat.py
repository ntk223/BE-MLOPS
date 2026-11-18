"""Unit and integration tests for the Interactive Advisor Chatbot feature."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from learnguard_backend.api.app import create_app
from learnguard_backend.db.entities import Base, Brief, ChatMessage, ChatSession, MLPrediction, User
from learnguard_backend.db.models import (
    BriefRecord,
    ChatMessageRecord,
    ChatSessionRecord,
    MLPredictionRecord,
)
from learnguard_backend.db.repository import (
    SQLBriefRepository,
    SQLChatRepository,
    SQLMLPredictionRepository,
)
from learnguard_backend.services.chat import (
    DEFAULT_QUICK_PROMPTS,
    AdvisorChatService,
    extract_sliding_window_history,
    prune_history_to_fit_token_budget,
)
from learnguard_backend.services.chat_context import (
    _format_activity_summary,
    _truncate_to_tokens,
    build_chat_context,
    format_system_message,
)
from learnguard_backend.settings import BackendSettings


class FakeLLM:
    """Mock LLM that returns pre-determined responses."""

    def __init__(self, content: str = "Sinh viên cần hỗ trợ về mặt học vụ."):
        self.content = content
        self.model_name = "test/fake-model"

    def complete(self, messages: list[dict[str, str]], **kwargs):
        return SimpleNamespace(
            content=self.content,
            model=self.model_name,
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=30, total_tokens=80),
        )


class AdvisorChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "student_key": "1234567890abcdef",
            "risk_score": 0.85,
            "status": "scored",
            "cutoff_day": 27,
            "top_signals": [
                {
                    "feature_name": "clicks__n_clicks_before_cutoff",
                    "observed_value": 15,
                    "contribution": 0.35,
                }
            ],
            "features": {
                "clicks__n_clicks_before_cutoff": 15,
                "avg_score_submitted": 42.0,
            },
        }

    def test_quick_prompts_high_risk(self):
        prompts = AdvisorChatService.get_quick_prompts(self.context)
        self.assertIn("Tại sao sinh viên này có nguy cơ rớt môn rất cao?", prompts)

    def test_quick_prompts_default(self):
        prompts = AdvisorChatService.get_quick_prompts({"risk_score": 0.3})
        self.assertEqual(prompts, DEFAULT_QUICK_PROMPTS)

    def test_reply_success_with_evidence_matching(self):
        fake_llm = FakeLLM(
            content="Sinh viên có clicks__n_clicks_before_cutoff quá thấp nên gặp rủi ro."
        )
        service = AdvisorChatService(llm=fake_llm)
        history = [
            ChatMessageRecord(
                message_id=1,
                session_id=10,
                role="advisor",
                content="Lý do là gì?",
            )
        ]
        reply, citations, tokens, model = service.reply(
            session_id=10,
            user_message="Giải thích thêm?",
            history=history,
            context=self.context,
        )

        self.assertIn("clicks__n_clicks_before_cutoff", reply)
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["feature_name"], "clicks__n_clicks_before_cutoff")
        self.assertEqual(tokens, 80)
        self.assertEqual(model, "test/fake-model")

    def test_reply_handles_llm_failure_gracefully(self):
        broken_llm = Mock()
        broken_llm.complete.side_effect = RuntimeError("OpenRouter connection failed")
        service = AdvisorChatService(llm=broken_llm)

        reply, citations, tokens, model = service.reply(
            session_id=10,
            user_message="Giải thích?",
            history=[],
            context=self.context,
        )

        self.assertIn("không thể kết nối tới mô hình AI", reply)
        self.assertEqual(citations, [])
        self.assertEqual(tokens, 0)
        self.assertEqual(model, "fallback")

    def test_finalize_to_brief_synthesizes_conversation(self):
        synthesis_json = json.dumps(
            {
                "summary": "Tóm tắt từ cuộc trao đổi cố vấn: sinh viên thiếu tương tác VLE.",
                "reasons": [
                    {
                        "text": "Số click trước ngày 27 quá thấp.",
                        "evidence_features": ["clicks__n_clicks_before_cutoff"],
                    }
                ],
                "support_suggestions": ["support_tutoring"],
                "limitations": ["Dữ liệu thu thập tới tuần 4."],
            },
            ensure_ascii=False,
        )
        fake_llm = FakeLLM(content=synthesis_json)
        service = AdvisorChatService(llm=fake_llm)
        history = [
            ChatMessageRecord(
                message_id=1,
                session_id=10,
                role="advisor",
                content="Sinh viên có tương tác không?",
            ),
            ChatMessageRecord(
                message_id=2,
                session_id=10,
                role="assistant",
                content="Không, số lượt click rất thấp.",
            ),
        ]
        draft = service.finalize_to_brief(
            session_id=10,
            history=history,
            context=self.context,
        )

        self.assertIn("thiếu tương tác VLE", draft.summary)
        self.assertEqual(len(draft.reasons), 1)
        self.assertEqual(draft.reasons[0].evidence_features, ["clicks__n_clicks_before_cutoff"])
        self.assertEqual(len(draft.evidence), 1)
        self.assertEqual(draft.evidence[0].feature_name, "clicks__n_clicks_before_cutoff")
        self.assertEqual(draft.evidence[0].observed_value, 15)

    def test_reply_with_enriched_context(self):
        fake_llm = FakeLLM(content="Dựa vào hướng dẫn cố vấn và hành vi click tuần 1-4...")
        service = AdvisorChatService(llm=fake_llm)
        enriched_ctx = {
            "prediction": self.context,
            "activity": {
                "tổng_click_VLE": 120,
                "bài_nộp": {"đã_nộp": 2, "chưa_nộp": 1, "điểm_TB": 55.0},
            },
            "guidelines": "Sổ tay cố vấn",
            "activity_available": True,
            "guidelines_available": True,
        }
        reply, citations, tokens, model = service.reply(
            session_id=10,
            user_message="Tình hình nộp bài ra sao?",
            history=[],
            context=enriched_ctx,
        )
        self.assertIn("Dựa vào hướng dẫn cố vấn", reply)
        self.assertEqual(tokens, 80)

    def test_finalize_to_brief_with_enriched_context(self):
        synthesis_json = json.dumps(
            {
                "summary": "Tóm tắt từ bối cảnh làm giàu: cần can thiệp bài tập.",
                "reasons": [
                    {
                        "text": "Điểm trung bình và số click thấp.",
                        "evidence_features": ["clicks__n_clicks_before_cutoff"],
                    }
                ],
                "support_suggestions": ["support_advisor_followup"],
                "limitations": [],
            },
            ensure_ascii=False,
        )
        service = AdvisorChatService(llm=FakeLLM(content=synthesis_json))
        enriched_ctx = {
            "prediction": self.context,
            "activity": {"tổng_click_VLE": 10},
            "guidelines": "Sổ tay cố vấn",
        }
        draft = service.finalize_to_brief(
            session_id=10,
            history=[],
            context=enriched_ctx,
        )
        self.assertIn("cần can thiệp bài tập", draft.summary)
        self.assertEqual(len(draft.reasons), 1)
        self.assertEqual(draft.reasons[0].evidence_features, ["clicks__n_clicks_before_cutoff"])


class ChatContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.prediction_rec = SimpleNamespace(
            student_key="1234567890abcdef",
            code_module="BBB",
            code_presentation="2013J",
            status="scored",
            risk_score=0.82,
            threshold=0.6,
            uncertainty=0.1,
            cutoff_day=27,
            top_signals_json=json.dumps([
                {
                    "feature_name": "clicks__n_clicks_before_cutoff",
                    "contribution": 0.35,
                    "direction": "increases_risk",
                }
            ]),
            features_json=json.dumps({
                "clicks__n_clicks_before_cutoff": 20,
                "score__avg_score": 38.0,
            }),
        )

    def test_build_chat_context_minimal(self):
        ctx = build_chat_context(self.prediction_rec, data_dir=None)
        self.assertIn("prediction", ctx)
        self.assertEqual(ctx["prediction"]["student_key"], "1234567890abcdef")
        self.assertEqual(ctx["prediction"]["risk_score"], 0.82)
        self.assertIsNone(ctx["activity"])
        self.assertFalse(ctx["activity_available"])
        self.assertTrue(ctx["guidelines_available"])
        self.assertIsNotNone(ctx["guidelines"])

    @patch("learnguard_backend.services.chat_context.build_student_activity")
    @patch("learnguard_backend.services.chat_context.resolve_student_id")
    def test_build_chat_context_with_activity(self, mock_resolve, mock_activity):
        mock_resolve.return_value = 12345
        mock_activity.return_value = {
            "cutoff_day": 27,
            "weeks": [
                {"label": "Trước khóa", "from_day": -14, "to_day": -1, "clicks": 5, "days_active": 1},
                {"label": "Tuần 1", "from_day": 0, "to_day": 6, "clicks": 50, "days_active": 4},
                {"label": "Tuần 2", "from_day": 7, "to_day": 13, "clicks": 40, "days_active": 3},
                {"label": "Tuần 3", "from_day": 14, "to_day": 20, "clicks": 10, "days_active": 1},
                {"label": "Tuần 4", "from_day": 21, "to_day": 27, "clicks": 5, "days_active": 1},
            ],
            "submissions": [
                {
                    "assessment_type": "TMA",
                    "assessment_day": 18,
                    "submitted_day": 22,
                    "score": 45,
                    "weight": 10,
                    "status": "submitted",
                },
                {
                    "assessment_type": "CMA",
                    "assessment_day": 25,
                    "submitted_day": None,
                    "score": None,
                    "weight": 5,
                    "status": "not_submitted",
                },
            ],
            "activity_breakdown": [
                {"category": "oucontent", "clicks": 80},
                {"category": "forumng", "clicks": 30},
            ],
        }

        from pathlib import Path
        ctx = build_chat_context(
            self.prediction_rec,
            data_dir=Path("fake_dir"),
        )
        self.assertTrue(ctx["activity_available"])
        act = ctx["activity"]
        self.assertEqual(act["tổng_click_VLE"], 110)
        self.assertEqual(act["bài_nộp"]["đã_nộp"], 1)
        self.assertEqual(act["bài_nộp"]["chưa_nộp"], 1)
        self.assertEqual(act["bài_nộp"]["điểm_TB"], 45.0)
        self.assertIn("bài_nộp_trễ", act)
        self.assertEqual(len(act["bài_nộp_trễ"]), 1)
        self.assertEqual(act["bài_nộp_trễ"][0]["trễ_ngày"], 4)

    def test_format_system_message(self):
        ctx = {
            "prediction": {"student_key": "test_key", "risk_score": 0.8},
            "activity": {"tổng_click_VLE": 50},
            "guidelines": "Nội dung sổ tay hướng dẫn",
        }
        msg = format_system_message(ctx, "SYSTEM PROMPT")
        self.assertIn("SYSTEM PROMPT", msg)
        self.assertIn("━━━ DỮ LIỆU DỰ ĐOÁN (JSON) ━━━", msg)
        self.assertIn("test_key", msg)
        self.assertIn("━━━ HÀNH VI HỌC TẬP (JSON) ━━━", msg)
        self.assertIn("tổng_click_VLE", msg)
        self.assertIn("━━━ HƯỚNG DẪN CỐ VẤN ━━━", msg)
        self.assertIn("Nội dung sổ tay hướng dẫn", msg)

    def test_token_budget_truncation(self):
        long_guidelines = "Chiến lược cố vấn " * 500  # ~9000 chars -> ~2250 tokens
        truncated = _truncate_to_tokens(long_guidelines, max_tokens=200)
        self.assertLessEqual(len(truncated), 200 * 4 + 100)
        self.assertIn("lược bớt", truncated)


class SQLChatRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        # Teach SQLite the PostgreSQL now() function
        from datetime import datetime, timezone
        from sqlalchemy import event
        @event.listens_for(self.engine, "connect")
        def _register_now(dbapi_conn, _):
            dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())

        # Create User, Brief, ChatSession, and ChatMessage tables for SQLite
        tables = [User.__table__, Brief.__table__, ChatSession.__table__, ChatMessage.__table__]
        for table in tables:
            # Strip PostgreSQL-specific server defaults for in-memory SQLite testing
            for col in table.columns:
                if col.server_default and "::" in str(col.server_default.arg):
                    col.server_default = None
        Base.metadata.create_all(self.engine, tables=tables)
        self.session_factory = sessionmaker(bind=self.engine)
        self.repo = SQLChatRepository(self.session_factory)
        self.brief_repo = SQLBriefRepository(self.session_factory)

        # Seed a test user
        with self.session_factory() as session:
            with session.begin():
                user = User(
                    user_id=1,
                    email="advisor@example.com",
                    full_name="Advisor Test",
                    role="advisor",
                    password_hash="hash",
                    active=True,
                )
                session.add(user)

    def tearDown(self):
        tables = [ChatMessage.__table__, ChatSession.__table__, Brief.__table__, User.__table__]
        Base.metadata.drop_all(self.engine, tables=tables)

    def test_create_and_get_session(self):
        record = ChatSessionRecord(
            session_id=None,
            student_key="1234567890abcdef",
            code_module="BBB",
            code_presentation="2013J",
            user_id=1,
            title="Tư vấn SV",
            status="active",
        )
        created = self.repo.create_session(record)
        self.assertIsNotNone(created.session_id)
        self.assertEqual(created.student_key, "1234567890abcdef")
        self.assertEqual(created.status, "active")

        fetched = self.repo.get_session(created.session_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.session_id, created.session_id)

    def test_get_active_session(self):
        record = ChatSessionRecord(
            session_id=None,
            student_key="abcdef1234567890",
            code_module="AAA",
            code_presentation="2014J",
            user_id=1,
            title="Active session",
            status="active",
        )
        self.repo.create_session(record)

        active = self.repo.get_active_session("AAA", "2014J", "abcdef1234567890")
        self.assertIsNotNone(active)
        self.assertEqual(active.title, "Active session")

        # Mark finalized
        self.repo.update_session(active.session_id, status="finalized")
        none_active = self.repo.get_active_session("AAA", "2014J", "abcdef1234567890")
        self.assertIsNone(none_active)

    def test_finalize_chat_session_links_to_brief(self):
        session_rec = self.repo.create_session(
            ChatSessionRecord(
                session_id=None,
                student_key="c4c2e78b683c603a",
                code_module="GGG",
                code_presentation="2014B",
                user_id=1,
                title="Phiên tư vấn hoàn chỉnh",
                status="active",
            )
        )
        # Create a brief using insert or bump_version as done in app.py finalize_chat_to_brief
        brief = self.brief_repo.insert(
            BriefRecord(
                brief_id=None,
                code_module=session_rec.code_module,
                code_presentation=session_rec.code_presentation,
                student_key=session_rec.student_key,
                version=1,
                content={"summary": "Tóm tắt từ chat"},
                citations=[],
                status="awaiting_review",
                created_by=1,
            )
        )
        self.assertIsNotNone(brief.brief_id)

        # Link to session
        updated = self.repo.update_session(
            session_rec.session_id, status="finalized", brief_id=brief.brief_id
        )
        self.assertEqual(updated.status, "finalized")
        self.assertEqual(updated.brief_id, brief.brief_id)

        fetched = self.repo.get_session(session_rec.session_id)
        self.assertEqual(fetched.status, "finalized")
        self.assertEqual(fetched.brief_id, brief.brief_id)

    def test_messages_flow(self):
        session_rec = self.repo.create_session(
            ChatSessionRecord(
                session_id=None,
                student_key="key1",
                code_module="BBB",
                code_presentation="2013J",
                user_id=1,
            )
        )
        msg1 = self.repo.add_message(
            ChatMessageRecord(
                message_id=None,
                session_id=session_rec.session_id,
                role="advisor",
                content="Chào trợ lý",
            )
        )
        msg2 = self.repo.add_message(
            ChatMessageRecord(
                message_id=None,
                session_id=session_rec.session_id,
                role="assistant",
                content="Chào cố vấn",
                cited_evidence=[{"feature_name": "feat1", "observed_value": 10}],
            )
        )

        messages = self.repo.list_messages(session_rec.session_id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].content, "Chào trợ lý")
        self.assertEqual(messages[1].content, "Chào cố vấn")
        self.assertEqual(len(messages[1].cited_evidence), 1)


class ChatAPIRouteRegistrationTests(unittest.TestCase):
    def test_chat_routes_are_registered(self):
        app = create_app(BackendSettings())
        route_paths = [r.path for r in app.routes]
        self.assertIn("/briefs/chat/quick-prompts", route_paths)
        self.assertIn("/briefs/chat/sessions", route_paths)
        self.assertIn("/briefs/chat/sessions/{session_id}", route_paths)
        self.assertIn("/briefs/chat/sessions/{session_id}/messages", route_paths)
        self.assertIn("/briefs/chat/sessions/{session_id}/finalize-brief", route_paths)


class SemanticLayerAndDualModeTests(unittest.TestCase):
    def test_semantic_layer_enrichment(self):
        from learnguard_backend.services.chat_context import apply_semantic_layer, resolve_feature_semantics

        sem = resolve_feature_semantics("clicks__n_clicks_before_cutoff")
        self.assertEqual(sem["ten"], "Tổng lượt tương tác học tập (VLE)")
        self.assertEqual(sem["don_vi"], "lượt click")

        sem_week = resolve_feature_semantics("clicks_week3")
        self.assertIn("tuần 3", sem_week["ten"])

        raw_context = {
            "student_key": "test_student",
            "risk_score": 0.85,
            "threshold": 0.5,
            "uncertainty": 0.12,
            "status": "scored",
            "top_signals": [
                {
                    "feature_name": "clicks__n_clicks_before_cutoff",
                    "observed_value": 15,
                    "contribution": 0.35,
                    "direction": "increases_risk",
                }
            ],
            "features": {
                "clicks__n_clicks_before_cutoff": 15,
                "avg_score_submitted": 42.0,
            },
        }

        enriched = apply_semantic_layer(raw_context)
        self.assertIn("y_nghia_thang_diem_va_chi_so", enriched)
        self.assertIn("tin_hieu_dong_gop_chinh (top_signals_tieng_viet)", enriched)
        self.assertIn("thong_ke_hoc_tap_chi_tiet (features_tieng_viet)", enriched)

        # Check scale explanations
        scales = enriched["y_nghia_thang_diem_va_chi_so"]
        self.assertIn("NGUY CƠ RỚT MÔN CAO", scales["diem_nguy_co (risk_score)"]["danh_gia"])
        self.assertEqual(scales["nguong_quyet_dinh (threshold)"]["gia_tri"], 0.5)

        # Check translated signals
        signals = enriched["tin_hieu_dong_gop_chinh (top_signals_tieng_viet)"]
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["ten_tieng_viet"], "Tổng lượt tương tác học tập (VLE)")
        self.assertIn("15 lượt click", signals[0]["gia_tri_thuc_te"])
        self.assertIn("TĂNG NGUY CƠ", signals[0]["chieu_tac_dong"])

    def test_classify_message_intent(self):
        from learnguard_backend.services.chat import classify_message_intent

        # Casual messages
        self.assertEqual(classify_message_intent("Xin chào"), "casual")
        self.assertEqual(classify_message_intent("Chào bạn nhé!"), "casual")
        self.assertEqual(classify_message_intent("Hi bot"), "casual")
        self.assertEqual(classify_message_intent("Bạn là ai?"), "casual")
        self.assertEqual(classify_message_intent("Bạn có thể giúp được gì?"), "casual")
        self.assertEqual(classify_message_intent("Cảm ơn bạn nhé"), "casual")
        self.assertEqual(classify_message_intent("Tạm biệt"), "casual")
        self.assertEqual(classify_message_intent("OK"), "casual")

        # Specific Intent Categories
        self.assertEqual(classify_message_intent("Tại sao em này có nguy cơ rớt môn?"), "why_risk")
        self.assertEqual(classify_message_intent("Lý do rủi ro là gì?"), "why_risk")
        self.assertEqual(classify_message_intent("Nên can thiệp gì cho sinh viên?"), "intervention")
        self.assertEqual(classify_message_intent("Cần làm gì để hỗ trợ sinh viên?"), "intervention")
        self.assertEqual(classify_message_intent("Soạn email động viên em này giúp tôi"), "draft_email")
        self.assertEqual(classify_message_intent("Tình hình nộp bài và điểm số ra sao?"), "general_analysis")
        self.assertEqual(classify_message_intent("Sinh viên click VLE bao nhiêu lượt?"), "general_analysis")
        self.assertEqual(classify_message_intent("Giải thích thêm về chỉ số?"), "general_analysis")

    def test_reply_casual_mode_concise_no_citations(self):
        from learnguard_backend.services.chat import AdvisorChatService

        fake_llm = FakeLLM(content="Xin chào Thầy/Cô! Tôi là Trợ lý Cố vấn Học thuật LearnGuard, sẵn sàng hỗ trợ phân tích hồ sơ sinh viên này.")
        service = AdvisorChatService(llm=fake_llm)
        context = {
            "student_key": "test_student",
            "risk_score": 0.85,
            "top_signals": [
                {"feature_name": "clicks__n_clicks_before_cutoff", "observed_value": 15, "contribution": 0.35}
            ],
            "features": {"clicks__n_clicks_before_cutoff": 15},
        }

        reply, citations, tokens, model = service.reply(
            session_id=1,
            user_message="Xin chào bạn",
            history=[],
            context=context,
        )

        self.assertIn("Trợ lý Cố vấn Học thuật", reply)
        self.assertEqual(citations, [], "Chế độ giao tiếp thông thường không được trích dẫn bằng chứng dữ liệu")

    def test_reply_structured_output_json_parsing_and_evidence(self):
        from learnguard_backend.services.chat import AdvisorChatService

        structured_response = json.dumps(
            {
                "direct_answer": "Sinh viên có 2 yếu tố rủi ro chính: lượt tương tác VLE quá thấp và nộp bài trễ.",
                "cited_features": ["clicks__n_clicks_before_cutoff", "date_registration_gap"],
                "next_step_suggestion": "Bạn có muốn xem quy trình can thiệp cho sinh viên này không?",
            },
            ensure_ascii=False,
        )
        fake_llm = FakeLLM(content=structured_response)
        service = AdvisorChatService(llm=fake_llm)
        context = {
            "student_key": "test_student",
            "risk_score": 0.88,
            "top_signals": [
                {
                    "feature_name": "clicks__n_clicks_before_cutoff",
                    "observed_value": 12,
                    "contribution": 0.42,
                    "direction": "increases_risk",
                },
                {
                    "feature_name": "date_registration_gap",
                    "observed_value": -3,
                    "contribution": 0.18,
                    "direction": "increases_risk",
                },
            ],
            "features": {
                "clicks__n_clicks_before_cutoff": 12,
                "date_registration_gap": -3,
            },
        }

        reply, citations, tokens, model = service.reply(
            session_id=1,
            user_message="Tại sao sinh viên này có nguy cơ rớt môn cao?",
            history=[],
            context=context,
        )

        self.assertIn("lượt tương tác VLE quá thấp", reply)
        self.assertIn("💡 *Gợi ý bước tiếp theo:*", reply)
        self.assertIn("xem quy trình can thiệp", reply)
        self.assertEqual(len(citations), 2, "Trích xuất chính xác 100% cả 2 đặc trưng từ cited_features")
        self.assertEqual(citations[0]["feature_name"], "clicks__n_clicks_before_cutoff")
        self.assertEqual(citations[0]["observed_value"], 12)
        self.assertEqual(citations[0]["contribution"], 0.42)

    def test_context_pruning_for_why_risk(self):
        from learnguard_backend.services.chat_context import prune_context_for_intent

        context = {
            "prediction": {
                "student_key": "student_1",
                "risk_score": 0.85,
                "top_signals": [
                    {"feature_name": "bad_clicks", "contribution": 0.35, "direction": "increases_risk"},
                    {"feature_name": "good_score", "contribution": -0.20, "direction": "decreases_risk"},
                ],
            },
            "guidelines": "Sổ tay quy trình can thiệp 4 bước...",
            "activity": {"clicks": 20},
        }

        pruned = prune_context_for_intent(context, intent="why_risk")
        self.assertIsNone(pruned["guidelines"], "HƯỚNG DẪN CỐ VẤN phải được tạm ẩn khi hỏi 'tại sao' để tránh bot tự chép đề xuất")
        signals = pruned["prediction"]["top_signals"]
        self.assertEqual(len(signals), 1, "Chỉ giữ các tín hiệu làm tăng rủi ro")
        self.assertEqual(signals[0]["feature_name"], "bad_clicks")

    def test_system_prompt_guardrails_and_completeness_rules(self):
        from learnguard_backend.services.chat import CHAT_SYSTEM_PROMPT, INTENT_DIRECTIVES, STRUCTURED_OUTPUT_PROMPT

        # Guardrail checks
        self.assertIn("Nguyên tắc đạo đức & Vai trò Cố vấn (Guardrail)", CHAT_SYSTEM_PROMPT)
        self.assertIn("KHÔNG phải thúc đẩy sinh viên bỏ học", CHAT_SYSTEM_PROMPT)
        self.assertIn("TUYỆT ĐỐI KHÔNG hùa theo hay tự ý soạn email khuyên sinh viên nghỉ học", CHAT_SYSTEM_PROMPT)
        self.assertIn("trao đổi 1-1", CHAT_SYSTEM_PROMPT)

        # Completeness / Anti-dangling intro checks
        self.assertIn("Dưới đây là...", CHAT_SYSTEM_PROMPT)
        self.assertIn("Dưới đây là...", STRUCTURED_OUTPUT_PROMPT)
        self.assertIn("Dưới đây là...", INTENT_DIRECTIVES["intervention"])
        self.assertIn("Dưới đây là...", INTENT_DIRECTIVES["draft_email"])


class ChatSlidingWindowTests(unittest.TestCase):
    def setUp(self):
        # 5 Q&A pairs (10 messages)
        self.history = [
            ChatMessageRecord(message_id=1, session_id=1, role="advisor", content="Câu hỏi 1"),
            ChatMessageRecord(message_id=2, session_id=1, role="assistant", content="Trả lời 1"),
            ChatMessageRecord(message_id=3, session_id=1, role="advisor", content="Câu hỏi 2"),
            ChatMessageRecord(message_id=4, session_id=1, role="assistant", content="Trả lời 2"),
            ChatMessageRecord(message_id=5, session_id=1, role="advisor", content="Câu hỏi 3"),
            ChatMessageRecord(message_id=6, session_id=1, role="assistant", content="Trả lời 3"),
            ChatMessageRecord(message_id=7, session_id=1, role="advisor", content="Câu hỏi 4"),
            ChatMessageRecord(message_id=8, session_id=1, role="assistant", content="Trả lời 4"),
            ChatMessageRecord(message_id=9, session_id=1, role="advisor", content="Câu hỏi 5"),
            ChatMessageRecord(message_id=10, session_id=1, role="assistant", content="Trả lời 5"),
        ]

    def test_extract_sliding_window_retains_last_3_pairs(self):
        window = extract_sliding_window_history(self.history, max_pairs=3)
        self.assertEqual(len(window), 6)
        self.assertEqual(window[0].content, "Câu hỏi 3")
        self.assertEqual(window[1].content, "Trả lời 3")
        self.assertEqual(window[2].content, "Câu hỏi 4")
        self.assertEqual(window[3].content, "Trả lời 4")
        self.assertEqual(window[4].content, "Câu hỏi 5")
        self.assertEqual(window[5].content, "Trả lời 5")

    def test_extract_sliding_window_retains_last_2_pairs(self):
        window = extract_sliding_window_history(self.history, max_pairs=2)
        self.assertEqual(len(window), 4)
        self.assertEqual(window[0].content, "Câu hỏi 4")
        self.assertEqual(window[1].content, "Trả lời 4")
        self.assertEqual(window[2].content, "Câu hỏi 5")
        self.assertEqual(window[3].content, "Trả lời 5")

    def test_extract_sliding_window_excludes_current_persisted_user_message(self):
        # When user's current message has already been inserted into history at the tail
        persisted_history = list(self.history) + [
            ChatMessageRecord(message_id=11, session_id=1, role="advisor", content="Câu hỏi 6 đang hỏi")
        ]
        window = extract_sliding_window_history(
            persisted_history,
            current_user_message="Câu hỏi 6 đang hỏi",
            max_pairs=3,
        )
        self.assertEqual(len(window), 6)
        # Should contain pairs 3, 4, 5, excluding pair 6 which is the current message
        self.assertEqual(window[0].content, "Câu hỏi 3")
        self.assertEqual(window[-1].content, "Trả lời 5")
        self.assertNotIn("Câu hỏi 6 đang hỏi", [m.content for m in window])

    def test_extract_sliding_window_fewer_than_max_pairs(self):
        short_history = [
            ChatMessageRecord(message_id=1, session_id=1, role="advisor", content="Chào bạn"),
            ChatMessageRecord(message_id=2, session_id=1, role="assistant", content="Chào cố vấn"),
        ]
        window = extract_sliding_window_history(short_history, max_pairs=3)
        self.assertEqual(len(window), 2)
        self.assertEqual(window[0].content, "Chào bạn")

    def test_prune_history_to_fit_token_budget_drops_oldest(self):
        # Simulate long messages
        long_window = [
            ChatMessageRecord(message_id=1, session_id=1, role="advisor", content="A" * 1000),
            ChatMessageRecord(message_id=2, session_id=1, role="assistant", content="B" * 1000),
            ChatMessageRecord(message_id=3, session_id=1, role="advisor", content="C" * 1000),
            ChatMessageRecord(message_id=4, session_id=1, role="assistant", content="D" * 1000),
        ]
        # Low max_input_tokens budget that forces pruning
        pruned = prune_history_to_fit_token_budget(
            system_content="System context",
            window_history=long_window,
            user_message="Current question",
            max_input_tokens=1000,
        )
        self.assertLess(len(pruned), len(long_window))
        # Ensure newest messages are preserved over oldest
        self.assertEqual(pruned[-1].content, "D" * 1000)

    def test_reply_uses_sliding_window_history_end_to_end(self):
        recorded_messages = []

        class InspectingLLM:
            model_name = "mock/model"

            def complete(self, messages, **kwargs):
                nonlocal recorded_messages
                recorded_messages = list(messages)
                return SimpleNamespace(
                    content='{"direct_answer": "Đã phân tích 3 cặp gần nhất.", "cited_features": [], "next_step_suggestion": ""}',
                    model="mock/model",
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                )

        service = AdvisorChatService(llm=InspectingLLM(), max_history_pairs=3)
        context = {
            "prediction": {
                "student_key": "sv_123",
                "risk_score": 0.75,
                "top_signals": [],
                "features": {},
            }
        }

        reply, citations, tokens, model = service.reply(
            session_id=1,
            user_message="Câu hỏi mới nhất",
            history=self.history,
            context=context,
        )

        # Inspect messages passed to LLM
        # Expected:
        # [0]: system message
        # [1..6]: 3 pairs (Câu hỏi 3, Trả lời 3, Câu hỏi 4, Trả lời 4, Câu hỏi 5, Trả lời 5)
        # [7]: user message "Câu hỏi mới nhất"
        self.assertEqual(len(recorded_messages), 8)
        self.assertEqual(recorded_messages[0]["role"], "system")
        self.assertEqual(recorded_messages[1]["content"], "Câu hỏi 3")
        self.assertEqual(recorded_messages[2]["content"], "Trả lời 3")
        self.assertEqual(recorded_messages[3]["content"], "Câu hỏi 4")
        self.assertEqual(recorded_messages[4]["content"], "Trả lời 4")
        self.assertEqual(recorded_messages[5]["content"], "Câu hỏi 5")
        self.assertEqual(recorded_messages[6]["content"], "Trả lời 5")
        self.assertEqual(recorded_messages[7]["role"], "user")
        self.assertEqual(recorded_messages[7]["content"], "Câu hỏi mới nhất")


if __name__ == "__main__":
    unittest.main()
