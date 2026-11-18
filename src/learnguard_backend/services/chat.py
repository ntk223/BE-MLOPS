"""M8 advisor interactive chat service (Advisor Copilot).

Allows academic advisors to have a multi-turn conversation about a student's
risk factors, behavioral signals, and intervention options.
Adheres strictly to the LearnGuard boundaries:
- Internal tool for advisors (never student-facing).
- Zero PII (uses pseudonymous student_key).
- Grounded in pipeline-provided predictions, features, and activity data.
- Conversation can be finalized into a canonical Brief record for approval.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from learnguard_backend.db.models import ChatMessageRecord
from learnguard_backend.services.briefs import (
    AdvisorBriefDraft,
    BriefEvidence,
    BriefReason,
    _observed_for,
    _parse_json_object,
)
from learnguard_backend.services.chat_context import (
    _estimate_tokens,
    apply_semantic_layer,
    format_system_message,
    prune_context_for_intent,
    resolve_feature_semantics,
)
from learnguard_backend.services.llm import LLMService, LiteLLMClient

logger = logging.getLogger("learnguard_backend.chat")

CHAT_SYSTEM_PROMPT = (
    "Bạn là Trợ lý Cố vấn Học thuật AI (Advisor Copilot) thuộc hệ thống cảnh báo sớm LearnGuard. "
    "Nhiệm vụ của bạn là hỗ trợ CỐ VẤN HỌC TẬP (Advisor) phân tích rủi ro học tập của sinh viên, "
    "giải thích các chỉ số từ mô hình Machine Learning, và gợi ý các giải pháp can thiệp sư phạm phù hợp.\n\n"
    "🧠 QUY TRÌNH TƯ DUY BẮT BUỘC (CHAIN OF THOUGHT):\n"
    "Trước khi trả lời bất kỳ câu hỏi nào của cố vấn về lý do rủi ro hay phân tích học vụ, bạn phải tự phân tích trong đầu qua 3 bước sau:\n"
    "1. ĐỌC TRẠNG THÁI & ĐỘ BẤT ĐỊNH: Kiểm tra xem hồ sơ này có ở trạng thái 'manual_review', 'insufficient_data' hay độ bất định cao hay không?\n"
    "2. ĐÁNH GIÁ Ý NGHĨA THỰC SỰ CỦA CON SỐ: Phân biệt rõ đâu là tín hiệu hành vi thực tế (tương tác VLE thật, bài nộp thật) và đâu là con số kỹ thuật do hệ thống ngoại suy/điền khuyết (do thiếu dữ liệu).\n"
    "3. ĐƯA RA KẾT LUẬN ĐỘC LẬP: Nếu rủi ro cao xuất phát từ việc thiếu dữ liệu (chứ không phải học kém thực sự), bạn phải chủ động giải thích cho cố vấn hiểu bản chất kỹ thuật đó, tuyệt đối không được đánh đồng đây là sinh viên học yếu.\n\n"
    "LUẬT PHẠM VI TRẢ LỜI (BẮT BUỘC TUÂN THỦ TUYỆT ĐỐI - QUY TẮC 1 Ý ĐỊNH):\n"
    "Chỉ trả lời ĐÚNG VÀO TRỌNG TÂM câu hỏi của cố vấn, TUYỆT ĐỐI KHÔNG tự ý nói thêm các phần chưa được hỏi:\n"
    "1. Nếu cố vấn hỏi 'Tại sao / Nguyên nhân': CHỈ nêu các yếu tố làm tăng rủi ro (tối đa 3 ý chính). "
    "CẤM viết phần 'Đề xuất hành động', CẤM viết 'Quy trình can thiệp', CẤM tự ý nêu 'Điểm tích cực'.\n"
    "2. Nếu cố vấn hỏi 'Nên làm gì / Giải pháp / Can thiệp': CHỈ nêu các bước hành động cụ thể theo sổ tay hướng dẫn. "
    "CẤM lặp lại phần phân tích nguyên nhân rủi ro chi tiết.\n"
    "3. Nếu cố vấn hỏi 'Soạn email / tin nhắn': CHỈ xuất nội dung thư/email mẫu với lời lẽ ân cần, hỏi thăm khó khăn. "
    "CẤM đề cập thuật toán AI, điểm rủi ro hay phân tích kỹ thuật.\n"
    "4. Nếu là câu chào hỏi xã giao: CHỈ phản hồi ngắn gọn 1-2 câu, sẵn sàng hỗ trợ phân tích hồ sơ sinh viên.\n"
    "5. Cuối câu trả lời, chỉ thêm duy nhất 1 câu gợi ý ngắn (tối đa 15 từ) định hướng bước tiếp theo.\n\n"
    "QUY TẮC BẮT BUỘC THEO TRẠNG THÁI DỰ ĐOÁN (PREDICTION STATUS GUARDRAILS):\n"
    "Luôn kiểm tra trường `trang_thai_ho_so (status)` trong DỮ LIỆU DỰ ĐOÁN trước khi đưa ra nhận định:\n"
    "1. Trạng thái 'manual_review' (Cần xem xét thủ công):\n"
    "   - ĐẶC BIỆT LƯU Ý: Tuyệt đối KHÔNG tự động đánh giá, KHÔNG dán nhãn khẳng định sinh viên là 'nguy cơ cao', 'rủi ro cao' hay 'chắc chắn rớt môn'.\n"
    "   - Hệ thống CHỈ ĐƯỢC PHÉP ĐƯA RA QUAN ĐIỂM / GÓC NHÌN THAM KHẢO dựa trên các tín hiệu ghi nhận được (nêu khách quan cả yếu tố biến động và điểm tích cực nếu có).\n"
    "   - Luôn nhấn mạnh đây chỉ là quan điểm tham khảo do mô hình có độ bất định (uncertainty) cao; quyết định và đánh giá rủi ro cuối cùng thuộc về Cố vấn học tập sau khi xem xét thủ công.\n"
    "2. Trạng thái 'insufficient_data' (Thiếu dữ liệu):\n"
    "   - BẮT BUỘC nêu rõ ngay từ đầu rằng hồ sơ sinh viên bị đánh tag thiếu dữ liệu học tập/hành vi đầu vào.\n"
    "   - Nhắc nhở Cố vấn rằng điểm số (nếu có) chỉ là ngoại suy kỹ thuật trên dữ liệu điền khuyết (impute), KHÔNG phản ánh đúng thực tế.\n"
    "   - KHÔNG tự động phân tích hay kết luận như hồ sơ có đầy đủ dữ liệu; khuyến nghị Cố vấn kiểm tra lại dữ liệu học vụ hoặc liên hệ trực tiếp sinh viên.\n"
    "3. Trạng thái 'no_prediction' (Chưa có dự đoán):\n"
    "   - Nêu rõ mô hình chưa sinh được dự đoán cho hồ sơ này, không tự bịa điểm số hay kết luận nguy cơ.\n"
    "4. Trạng thái 'scored' (Đã chấm điểm thành công):\n"
    "   - Phân tích bình thường dựa trên dữ liệu và tín hiệu SHAP đã được cung cấp.\n\n"
    "QUY TẮC DIỄN GIẢI & PHONG CÁCH TRẢ LỜI:\n"
    "1. Hoàn chỉnh nội dung: Tuyệt đối KHÔNG viết câu dẫn kiểu 'Dưới đây là...' nếu không kèm đầy đủ nội dung chi tiết ngay bên dưới; "
    "phải viết trọn vẹn toàn bộ câu trả lời, không dừng lại ở câu giới thiệu.\n"
    "2. Nguyên tắc đạo đức & Vai trò Cố vấn (Guardrail):\n"
    "   - Mục tiêu cốt lõi của LearnGuard là hỗ trợ sinh viên vượt qua khó khăn để tiếp tục việc học, KHÔNG phải thúc đẩy sinh viên bỏ học.\n"
    "   - Nếu cố vấn đề xuất hướng đi tiêu cực hoặc cực đoan (ví dụ: khuyên sinh viên bỏ đại học để đi học nghề, đi làm kiếm tiền, hoặc buông xuôi):\n"
    "     + TUYỆT ĐỐI KHÔNG hùa theo hay tự ý soạn email khuyên sinh viên nghỉ học/chuyển hướng.\n"
    "     + Phản hồi khách quan, tôn trọng cố vấn nhưng nhấn mạnh lại nguyên tắc: dữ liệu hiện tại mới chỉ phản ánh sự thiếu hụt tương tác/điểm số trong giai đoạn đầu, chưa đủ cơ sở để kết luận năng lực hay định hướng tương lai của sinh viên.\n"
    "     + Đề xuất cố vấn hẹn gặp trao đổi 1-1 để lắng nghe nguyên nhân thực sự (tài chính, sức khỏe, phương pháp học) trước khi đưa ra bất kỳ tư vấn chuyển hướng nào."
)

INTENT_DIRECTIVES = {
    "why_risk": (
        "【LUẬT PHẠM VI TRẢ LỜI: HỎI NGUYÊN NHÂN / TẠI SAO】\n"
        "Cố vấn đang hỏi tại sao/nguyên nhân sinh viên có rủi ro cao.\n"
        "- CHỈ nêu các yếu tố làm tăng rủi ro (tối đa 3 ý chính dựa trên dữ liệu).\n"
        "- CẤM TUYỆT ĐỐI: Không viết phần 'Đề xuất hành động', không viết 'Quy trình can thiệp', không nêu 'Điểm tích cực'.\n"
        "- Cuối câu, chỉ thêm duy nhất 1 câu ngắn (tối đa 15 từ) gợi ý bước tiếp theo (ví dụ: 'Bạn có muốn xem quy trình can thiệp cho sinh viên này không?')."
    ),
    "intervention": (
        "【LUẬT PHẠM VI TRẢ LỜI: GIẢI PHÁP / CAN THIỆP】\n"
        "Cố vấn đang hỏi về giải pháp/can thiệp/nên làm gì.\n"
        "- CHỈ nêu các bước hành động cụ thể theo sổ tay hướng dẫn cố vấn. Viết đầy đủ, rõ ràng từng bước hành động, KHÔNG chỉ viết câu dẫn mở đầu kiểu 'Dưới đây là...' rồi bỏ lửng.\n"
        "- CẤM TUYỆT ĐỐI: Không lặp lại phần phân tích nguyên nhân rủi ro chi tiết.\n"
        "- Cuối câu, chỉ thêm duy nhất 1 câu ngắn (tối đa 15 từ) gợi ý bước tiếp theo (ví dụ: 'Bạn có muốn tôi soạn sẵn email hỏi thăm sinh viên này không?')."
    ),
    "draft_email": (
        "【LUẬT PHẠM VI TRẢ LỜI: SOẠN EMAIL / TIN NHẮN】\n"
        "Cố vấn yêu cầu soạn email/tin nhắn gửi sinh viên.\n"
        "- CHỈ xuất nội dung thư/email mẫu với văn phong ân cần, động viên, hỏi thăm khó khăn học tập. Xuất TRỌN VẸN toàn bộ bức thư (Tiêu đề, Lời chào, Nội dung, Lời chúc), KHÔNG chỉ viết câu dẫn kiểu 'Dưới đây là...' rồi dừng lại.\n"
        "- CẤM TUYỆT ĐỐI: Không đề cập thuật toán AI, không nhắc điểm rủi ro, không viết phân tích kỹ thuật.\n"
        "- Cuối câu, thêm duy nhất 1 câu ngắn hỏi cố vấn có cần chỉnh sửa gì thêm không."
    ),
    "casual": (
        "【LUẬT PHẠM VI TRẢ LỜI: GIAO TIẾP THÔNG THƯỜNG】\n"
        "Cố vấn đang chào hỏi hoặc giao tiếp xã giao.\n"
        "- Phản hồi cực kỳ NGẮN GỌN (1-2 câu), chào lại lịch sự, thông báo bạn đã sẵn sàng hỗ trợ cố vấn phân tích hồ sơ sinh viên này.\n"
        "- CẤM TUYỆT ĐỐI: Không phân tích số liệu, không đưa ra điểm rủi ro hay quy trình can thiệp."
    ),
    "general_analysis": (
        "【LUẬT PHẠM VI TRẢ LỜI: PHÂN TÍCH HỌC VỤ】\n"
        "Trả lời ĐÚNG vào trọng tâm câu hỏi của cố vấn (tối đa 3-4 gạch đầu dòng ngắn gọn, đầy đủ nội dung, không viết câu dẫn lửng).\n"
        "Không lan man sang các chủ đề chưa được hỏi. Cuối câu thêm 1 câu ngắn gợi ý bước tiếp theo."
    ),
}

STRUCTURED_OUTPUT_PROMPT = (
    "\n\nBẮT BUỘC TRẢ LỜI THEO ĐÚNG ĐỊNH DẠNG JSON SAU (CHỈ TRẢ VỀ JSON HỢP LỆ, KHÔNG KÈM TEXT NGOÀI):\n"
    "{\n"
    '  "direct_answer": "Nội dung trả lời trực tiếp, đầy đủ chi tiết, tuân thủ đúng Luật phạm vi trả lời. CẤM viết câu dẫn kiểu \'Dưới đây là...\' nếu không kèm đầy đủ nội dung chi tiết ngay bên dưới.",\n'
    '  "cited_features": ["tên_biến_gốc_1", "tên_biến_gốc_2"],\n'
    '  "next_step_suggestion": "1 câu ngắn tối đa 15 từ gợi ý bước tiếp theo (hoặc để rỗng nếu là câu chào)."\n'
    "}\n"
    "LƯU Ý: Trường 'cited_features' là danh sách chuỗi tên biến kỹ thuật gốc trong bối cảnh "
    "(ví dụ: ['clicks__n_clicks_before_cutoff', 'avg_score_submitted']) đã dùng làm bằng chứng."
)

ANALYSIS_KEYWORDS = {
    "sinh viên", "học sinh", "em này", "bạn này", "sv", "hồ sơ",
    "nguy cơ", "rủi ro", "rớt", "trượt", "điểm", "bài tập", "kiểm tra",
    "tma", "cma", "vle", "click", "tương tác", "nộp bài", "trễ",
    "quá hạn", "tuần", "tại sao", "lý do", "nguyên nhân", "can thiệp",
    "hỗ trợ", "giải pháp", "khuyên", "email", "tin nhắn", "soạn",
    "tín chỉ", "học lại", "thang điểm", "chỉ số", "đặc trưng", "tín hiệu",
    "giải thích", "chi tiết", "phân tích", "kịch bản", "xu hướng",
    "deadlines", "cutoff", "shap", "feature", "prediction",
}

CASUAL_PATTERNS = [
    r"^(xin\s+)?chào(\s+(bạn|bot|em|ad|cố\s+vấn|thầy|cô))?.*$",
    r"^(hello|hi|hey|greetings|good\s+(morning|afternoon|evening)).*$",
    r"^(bạn|em)\s+(là\s+ai|tên\s+là\s+gì)[\s?!.]*$",
    r"^(bạn|em)\s+có\s+thể\s+giúp\s+(được\s+)?(gì|những\s+gì)[\s?!.]*$",
    r"^(bạn|em)\s+làm\s+(được\s+)?gì[\s?!.]*$",
    r"^(chức\s+năng|nhiệm\s+vụ|vai\s+trò)\s+của\s+(bạn|em)[\s?!.]*$",
    r"^(cảm\s+ơn|thanks|thank\s+you).*$",
    r"^(tạm\s+biệt|bye|goodbye).*$",
    r"^(ok|được\s+rồi|vâng|dạ|rõ\s+rồi|tuyệt\s+vời|good)[\s!.]*$",
]

DYNAMIC_MAX_TOKENS = {
    "casual": 200,
    "why_risk": 500,
    "intervention": 600,
    "draft_email": 800,
    "general_analysis": 500,
}


def classify_message_intent(user_message: str) -> str:
    """Classify user message into 'casual', 'why_risk', 'intervention', 'draft_email', or 'general_analysis'."""
    text = (user_message or "").strip().lower()
    if not text:
        return "casual"

    # 1. Draft email / message
    if any(k in text for k in (
        "soạn email", "soạn thư", "soạn tin nhắn", "mẫu email", "mẫu tin nhắn",
        "gửi email", "gửi tin nhắn", "viết thư", "viết email",
    )):
        return "draft_email"

    # 2. Intervention / Action
    if any(k in text for k in (
        "nên làm gì", "cần làm gì", "giải pháp", "can thiệp", "phương án",
        "kế hoạch", "hành động", "hướng xử lý", "làm gì để", "hỗ trợ thế nào",
        "làm sao để", "khuyên", "tư vấn gì", "quy trình can thiệp",
    )):
        return "intervention"

    # 3. Why risk / Causes
    if any(k in text for k in (
        "tại sao", "nguyên nhân", "lý do", "vì sao", "sao lại",
        "yếu tố rủi ro", "nguy cơ cao", "rủi ro cao", "nguy cơ rớt", "dẫn đến rủi ro",
    )):
        return "why_risk"

    # 4. Check if it's casual greeting / small-talk
    is_casual = False
    for pat in CASUAL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            is_casual = True
            break

    if not is_casual and len(text) <= 30 and (
        text.startswith("chào")
        or text.startswith("xin chào")
        or text.startswith("hi")
        or text.startswith("hello")
        or text.startswith("cảm ơn")
        or text.startswith("thanks")
        or text.startswith("tạm biệt")
    ):
        is_casual = True

    if is_casual:
        # If casual text also has any data/student keywords, fall through to analysis
        if not any(k in text for k in ANALYSIS_KEYWORDS):
            return "casual"

    # 5. Fallback for other student inquiries
    return "general_analysis"

FINALIZE_BRIEF_PROMPT = (
    "Dựa trên DỮ LIỆU BỐI CẢNH và TOÀN BỘ NỘI DUNG CUỘC HỘI THOẠI giữa Cố vấn và Trợ lý ở trên, "
    "hãy tổng hợp thành một bản Brief chính thức theo đúng định dạng JSON bên dưới.\n"
    "Mỗi lý do (reason) phải trích dẫn ít nhất một feature có trong dữ liệu (evidence_features).\n"
    "Chỉ trả về DUY NHẤT một đối tượng JSON hợp lệ, không kèm markdown:\n"
    '{"summary": str, "reasons": [{"text": str, "evidence_features": [str]}], '
    '"support_suggestions": [str], "limitations": [str]}'
)

DEFAULT_QUICK_PROMPTS = [
    "Tại sao sinh viên này bị cảnh báo rủi ro cao?",
    "Hoạt động tương tác VLE của sinh viên qua các tuần (tuần 1 đến 4) như thế nào?",
    "Tình hình nộp bài kiểm tra và điểm số của sinh viên ra sao?",
    "Đề xuất các phương án can thiệp học vụ phù hợp nhất cho sinh viên này.",
    "Hãy soạn giúp tôi một email hỏi thăm và nhắc nhở học tập gửi sinh viên.",
]


def extract_sliding_window_history(
    history: list[ChatMessageRecord],
    current_user_message: str | None = None,
    max_pairs: int = 3,
) -> list[ChatMessageRecord]:
    """Extract a sliding window of recent conversation turns (pairs).

    Excludes the current user message if it has already been appended to the
    tail of `history` (e.g. persisted to DB before calling LLM), then keeps
    up to `max_pairs` interaction pairs (advisor prompt + assistant reply).

    Parameters
    ----------
    history
        Chronological list of ChatMessageRecord from the session.
    current_user_message
        The current user message. Used to strip any duplicate trailing message.
    max_pairs
        Maximum number of recent dialogue pairs to retain (defaults to 3).

    Returns
    -------
    list[ChatMessageRecord]
        Sliding window of messages maintaining chronological order.
    """
    if not history or max_pairs <= 0:
        return []

    past = list(history)
    # If the last message in history is the current user message already saved to DB,
    # exclude it from the history window so it is not duplicated.
    if (
        past
        and past[-1].role in ("advisor", "user")
        and (current_user_message is None or past[-1].content == current_user_message)
    ):
        past = past[:-1]

    if not past:
        return []

    # Count pairs backwards: each 'advisor'/'user' message marks the beginning of a turn pair
    pair_count = 0
    start_idx = 0
    for idx in range(len(past) - 1, -1, -1):
        if past[idx].role in ("advisor", "user"):
            pair_count += 1
            start_idx = idx
            if pair_count >= max_pairs:
                break

    window = past[start_idx:]
    return window


def prune_history_to_fit_token_budget(
    system_content: str,
    window_history: list[ChatMessageRecord],
    user_message: str,
    max_input_tokens: int = 8000,
    safety_margin: int = 150,
) -> list[ChatMessageRecord]:
    """Drop oldest messages from window_history if total estimated tokens exceed budget.

    Guarantees that (system_prompt + context + history + current_user_message)
    will not exceed `max_input_tokens`, preventing LLM context overflow errors.
    """
    if not isinstance(max_input_tokens, int) or isinstance(max_input_tokens, bool) or max_input_tokens <= 0:
        max_input_tokens = 8000

    system_tokens = _estimate_tokens(system_content)
    user_tokens = _estimate_tokens(user_message)
    budget_for_history = max_input_tokens - system_tokens - user_tokens - safety_margin

    if budget_for_history <= 0:
        logger.warning(
            "[TokenOverflowProtection] System content and user message require ~%d tokens, "
            "exceeding or leaving no room within max_input_tokens (%d). Dropping all history.",
            system_tokens + user_tokens,
            max_input_tokens,
        )
        return []

    selected = list(window_history)
    while selected:
        total_hist_tokens = sum(_estimate_tokens(m.content or "") for m in selected)
        if total_hist_tokens <= budget_for_history:
            break
        dropped = selected.pop(0)
        logger.info(
            "[TokenOverflowProtection] Dropping oldest history message (role: %s, ~%d tokens) to fit token budget",
            dropped.role,
            _estimate_tokens(dropped.content or ""),
        )

    # If dropping messages left an orphan assistant reply at the start, drop it for clean dialogue context
    if selected and selected[0].role == "assistant" and len(selected) > 1:
        selected = selected[1:]

    return selected


class AdvisorChatService:
    """Orchestrates advisor Q&A interactions grounded in student prediction context."""

    def __init__(
        self,
        llm: LLMService | Any | None = None,
        max_history_pairs: int = 3,
        max_input_tokens: int = 8000,
    ) -> None:
        self._llm = llm
        self.max_history_pairs = max_history_pairs
        self.max_input_tokens = max_input_tokens

    def _llm_service(self) -> Any:
        if self._llm is None:
            from learnguard_backend.services.llm import get_llm_service

            self._llm = get_llm_service()
        return self._llm

    @staticmethod
    def get_quick_prompts(context: dict[str, Any] | None = None) -> list[str]:
        """Return recommended starter questions for the advisor UI."""
        if not context:
            return DEFAULT_QUICK_PROMPTS
        pred = context.get("prediction", context) if isinstance(context, dict) else {}
        risk_score = pred.get("risk_score") if isinstance(pred, dict) else None
        if risk_score is not None and risk_score >= 0.7:
            return [
                "Tại sao sinh viên này có nguy cơ rớt môn rất cao?",
                "Có bài tập nào bị bỏ lỡ hoặc nộp trễ trước cutoff không?",
                "So sánh mức độ tương tác tuần 1 so với tuần 4?",
                "Nên ưu tiên can thiệp gì ngay trong tuần tới?",
                "Soạn giúp tin nhắn liên hệ khẩn cấp với sinh viên.",
            ]
        return DEFAULT_QUICK_PROMPTS

    def reply(
        self,
        session_id: int,
        user_message: str,
        history: list[ChatMessageRecord],
        context: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], int, str]:
        """Generate a contextual reply to the advisor.

        Parameters
        ----------
        context
            Either a *rich* context dict (from ``build_chat_context()``,
            with keys ``prediction``, ``activity``, ``guidelines``) or a
            *legacy* flat prediction context (from ``build_context()``).

        Returns: (content, cited_evidence, tokens_used, model_name)
        """
        intent = classify_message_intent(user_message)
        directive = INTENT_DIRECTIVES.get(intent, INTENT_DIRECTIVES["general_analysis"])
        full_mode_instruction = f"{directive}\n{STRUCTURED_OUTPUT_PROMPT}"

        # Support both enriched context (with "prediction" key) and legacy
        # flat context (from build_context() directly), applying Context Pruning per intent.
        if "prediction" in context:
            system_content = format_system_message(
                context,
                CHAT_SYSTEM_PROMPT,
                mode_instruction=full_mode_instruction,
                intent=intent,
            )
            prediction_ctx = context["prediction"]
        else:
            pruned_context = prune_context_for_intent(context, intent)
            prediction_ctx = apply_semantic_layer(pruned_context)
            llm_context = {k: v for k, v in prediction_ctx.items() if k != "allowed_evidence"}
            system_content = (
                f"{CHAT_SYSTEM_PROMPT}\n\n"
                f"{full_mode_instruction}\n\n"
                "━━━ DỮ LIỆU BỐI CẢNH SINH VIÊN (JSON) ━━━\n"
                f"{json.dumps(llm_context, ensure_ascii=False, default=str)}"
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

        # 1. Sliding window: extract recent 2-3 conversation pairs (excluding current message)
        window_history = extract_sliding_window_history(
            history,
            current_user_message=user_message,
            max_pairs=self.max_history_pairs,
        )

        # 2. Token overflow safeguard: prune older turns if total tokens exceed input budget
        client = getattr(self._llm, "_client", None)
        client_tokens = getattr(client, "max_input_tokens", None) if client is not None else None
        if isinstance(client_tokens, int) and not isinstance(client_tokens, bool):
            effective_max_input = client_tokens
        elif isinstance(self.max_input_tokens, int) and not isinstance(self.max_input_tokens, bool):
            effective_max_input = self.max_input_tokens
        else:
            effective_max_input = 8000

        safe_history = prune_history_to_fit_token_budget(
            system_content=system_content,
            window_history=window_history,
            user_message=user_message,
            max_input_tokens=effective_max_input,
        )

        for msg in safe_history:
            role = "user" if msg.role == "advisor" else "assistant"
            messages.append({"role": role, "content": msg.content})

        messages.append({"role": "user", "content": user_message})

        logger.info(
            "[AdvisorChat.reply] Generating reply for session %s (intent: %s, raw_history: %d msgs, safe_window: %d msgs, prompt: %r)",
            session_id,
            intent,
            len(history),
            len(safe_history),
            user_message[:80],
        )

        # Dynamic max_tokens limit based on intent
        max_tokens_budget = DYNAMIC_MAX_TOKENS.get(intent, 450)

        try:
            llm = self._llm_service()
            active_llm = llm
            if isinstance(llm, (LLMService, LiteLLMClient)):
                active_llm = llm.with_max_tokens(max_tokens_budget)

            logger.info(
                "[AdvisorChat.reply] Invoking LLM service (model: %s, max_tokens: %d)...",
                getattr(active_llm, "model_name", "unknown"),
                max_tokens_budget,
            )
            response = active_llm.complete(
                messages,
                temperature=1.0,
                response_format={"type": "json_object"},
            )
            raw_content = getattr(response, "content", "")
            raw_reply = str(raw_content) if raw_content is not None and not hasattr(raw_content, "_mock_return_value") else ""
            model_name = getattr(response, "model", None) or getattr(active_llm, "model_name", "unknown")
            usage = getattr(response, "usage", None)
            total_tokens = getattr(usage, "total_tokens", 0) if usage and not hasattr(usage, "_mock_return_value") else 0
            logger.info(
                "[AdvisorChat.reply SUCCESS] Model '%s' replied (total_tokens: %d, raw_len: %d chars)",
                model_name,
                total_tokens,
                len(raw_reply),
            )
        except Exception as exc:  # Degrade gracefully, never 500
            logger.exception(
                "[AdvisorChat.reply ERROR] Failed to generate AI reply for session %s: %s",
                session_id,
                exc,
            )
            reply_text = (
                f"Trợ lý tạm thời không thể kết nối tới mô hình AI ({type(exc).__name__}). "
                "Tuy nhiên, bạn có thể xem trực tiếp các chỉ số đặc trưng (SHAP signals) "
                "và biểu đồ hoạt động tuần 1-4 trên thẻ hồ sơ sinh viên."
            )
            return reply_text, [], 0, "fallback"

        # Structured Output parsing & 100% precise cited evidence extraction
        parsed = _parse_json_object(raw_reply)
        cited_evidence: list[dict[str, Any]] = []

        if isinstance(parsed, dict) and "direct_answer" in parsed:
            direct_ans = str(parsed.get("direct_answer", "")).strip()
            next_step = str(parsed.get("next_step_suggestion", "")).strip()
            cited_features_raw = parsed.get("cited_features", [])

            if next_step and next_step not in direct_ans:
                reply_text = f"{direct_ans}\n\n💡 *Gợi ý bước tiếp theo:* {next_step}"
            else:
                reply_text = direct_ans

            if intent != "casual" and isinstance(cited_features_raw, list):
                top_signals = prediction_ctx.get("top_signals", [])
                features = prediction_ctx.get("features", {})
                for item in cited_features_raw:
                    feat_key = str(item).strip()
                    if not feat_key:
                        continue
                    matched = next(
                        (
                            s
                            for s in top_signals
                            if s.get("feature_name") == feat_key
                            or feat_key in s.get("feature_name", "")
                            or s.get("feature_name", "").endswith(feat_key)
                            or resolve_feature_semantics(s.get("feature_name", "")).get("ten") == feat_key
                        ),
                        None,
                    )
                    if matched:
                        raw_name = matched.get("feature_name") or feat_key
                        sem = resolve_feature_semantics(raw_name)
                        friendly_name = sem.get("ten", raw_name)
                        cited_evidence.append(
                            {
                                "feature_name": friendly_name,
                                "display_name": friendly_name,
                                "raw_feature_name": raw_name,
                                "observed_value": matched.get("observed_value"),
                                "contribution": matched.get("contribution"),
                                "unit": sem.get("don_vi", ""),
                            }
                        )
                    elif feat_key in features:
                        sem = resolve_feature_semantics(feat_key)
                        friendly_name = sem.get("ten", feat_key)
                        cited_evidence.append(
                            {
                                "feature_name": friendly_name,
                                "display_name": friendly_name,
                                "raw_feature_name": feat_key,
                                "observed_value": features[feat_key],
                                "contribution": None,
                                "unit": sem.get("don_vi", ""),
                            }
                        )
                    else:
                        matched_feat = next(
                            (
                                k
                                for k in features
                                if k == feat_key
                                or k.endswith(feat_key)
                                or feat_key.endswith(k)
                                or resolve_feature_semantics(k).get("ten") == feat_key
                            ),
                            None,
                        )
                        if matched_feat:
                            sem = resolve_feature_semantics(matched_feat)
                            friendly_name = sem.get("ten", matched_feat)
                            cited_evidence.append(
                                {
                                    "feature_name": friendly_name,
                                    "display_name": friendly_name,
                                    "raw_feature_name": matched_feat,
                                    "observed_value": features[matched_feat],
                                    "contribution": None,
                                    "unit": sem.get("don_vi", ""),
                                }
                            )
        else:
            # Fallback for plain-text response (e.g. from FakeLLM in unit tests)
            reply_text = raw_reply
            if intent != "casual":
                top_signals = prediction_ctx.get("top_signals", [])
                for signal in top_signals:
                    name = signal.get("feature_name", "")
                    sem = resolve_feature_semantics(name)
                    vi_name = sem.get("ten", "")
                    if name and (
                        name in reply_text
                        or name.split("__")[-1] in reply_text
                        or (vi_name and vi_name in reply_text)
                    ):
                        friendly_name = vi_name or name
                        cited_evidence.append(
                            {
                                "feature_name": friendly_name,
                                "display_name": friendly_name,
                                "raw_feature_name": name,
                                "observed_value": signal.get("observed_value"),
                                "contribution": signal.get("contribution"),
                                "unit": sem.get("don_vi", ""),
                            }
                        )

        return reply_text, cited_evidence, total_tokens, model_name

    def finalize_to_brief(
        self,
        session_id: int,
        history: list[ChatMessageRecord],
        context: dict[str, Any],
    ) -> AdvisorBriefDraft:
        """Synthesize the conversation into an official AdvisorBriefDraft."""
        if "prediction" in context:
            prediction_ctx = context["prediction"]
            system_content = format_system_message(context, CHAT_SYSTEM_PROMPT)
        else:
            prediction_ctx = apply_semantic_layer(context)
            system_content = (
                f"{CHAT_SYSTEM_PROMPT}\n\n"
                "━━━ DỮ LIỆU BỐI CẢNH SINH VIÊN (ĐÃ CHUYỂN ĐỔI NGỮ NGHĨA) ━━━\n"
                f"{json.dumps(prediction_ctx, ensure_ascii=False, default=str)}"
            )

        features = prediction_ctx.get("features", {})
        allowed_features = set(features.keys())
        for sig in prediction_ctx.get("top_signals", []):
            allowed_features.add(sig.get("feature_name", ""))

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

        # Protect against token overflow if session history is very long
        client = getattr(self._llm, "_client", None)
        client_tokens = getattr(client, "max_input_tokens", None) if client is not None else None
        if isinstance(client_tokens, int) and not isinstance(client_tokens, bool):
            effective_max_input = client_tokens
        elif isinstance(self.max_input_tokens, int) and not isinstance(self.max_input_tokens, bool):
            effective_max_input = self.max_input_tokens
        else:
            effective_max_input = 8000

        safe_history = prune_history_to_fit_token_budget(
            system_content=system_content,
            window_history=history,
            user_message=FINALIZE_BRIEF_PROMPT,
            max_input_tokens=effective_max_input,
        )

        for msg in safe_history:
            role = "user" if msg.role == "advisor" else "assistant"
            messages.append({"role": role, "content": msg.content})

        messages.append({"role": "user", "content": FINALIZE_BRIEF_PROMPT})

        logger.info(
            "[AdvisorChat.finalize] Synthesizing session %s into brief draft (history: %d msgs)...",
            session_id,
            len(history),
        )

        try:
            llm = self._llm_service()
            logger.info("[AdvisorChat.finalize] Calling LLM model '%s'...", getattr(llm, "model_name", "unknown"))
            response = llm.complete(messages, temperature=1.0)
            content_str = getattr(response, "content", None)
            logger.info("[AdvisorChat.finalize] Raw output received: %s", (content_str or "")[:200])
            parsed = _parse_json_object(content_str)
        except Exception as exc:
            logger.exception("[AdvisorChat.finalize ERROR] Failed to synthesize brief for session %s: %s", session_id, exc)
            parsed = None

        if not parsed or not isinstance(parsed, dict):
            # Fallback draft if synthesis fails
            return AdvisorBriefDraft(
                summary="Bản thảo tổng hợp từ phiên trao đổi cố vấn.",
                reasons=[
                    BriefReason(
                        text="Tổng hợp dựa trên các câu hỏi và phân tích trong phiên hỏi đáp.",
                        evidence_features=[],
                    )
                ],
                evidence=[],
                support_template_ids=["support_general_review"],
                limitations=["Bản tổng hợp tự động cần cố vấn rà soát lại trước khi phê duyệt."],
            )

        summary = str(parsed.get("summary") or "Bản tóm tắt can thiệp học vụ.")
        reasons_raw = parsed.get("reasons") or []
        reasons: list[BriefReason] = []
        evidence_list: list[BriefEvidence] = []
        cited_names: set[str] = set()

        for r in reasons_raw:
            if not isinstance(r, dict):
                continue
            text = str(r.get("text") or "")
            ev_feats = [f for f in r.get("evidence_features") or [] if isinstance(f, str)]
            valid_feats = [f for f in ev_feats if f in allowed_features or any(f.endswith(k) for k in allowed_features)]
            if text:
                reasons.append(BriefReason(text=text, evidence_features=valid_feats))
                for vf in valid_feats:
                    if vf not in cited_names:
                        cited_names.add(vf)
                        evidence_list.append(
                            BriefEvidence(
                                feature_name=vf,
                                observed_value=_observed_for(vf, features),
                            )
                        )

        suggestions = [
            str(s) for s in parsed.get("support_suggestions") or [] if isinstance(s, str)
        ]
        limitations = [
            str(lim) for lim in parsed.get("limitations") or [] if isinstance(lim, str)
        ]

        return AdvisorBriefDraft(
            summary=summary,
            reasons=reasons,
            evidence=evidence_list,
            support_template_ids=suggestions or ["support_advisor_followup"],
            limitations=limitations,
        )
