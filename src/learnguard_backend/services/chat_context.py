"""Rich context builder for Advisor Copilot chat sessions.

Assembles a comprehensive, grounded context for the LLM by combining:
  1. **Prediction data** — risk score, SHAP top signals, features (from
     ``build_context()`` in ``briefs.py``).
  2. **Student activity data** — weekly VLE clicks, submission history,
     activity breakdown by resource type (from ``build_student_activity()``
     in ``student_activity.py``).
  3. **Advisor guidelines** — static handbook content loaded once from
     ``advisor_guidelines.md``.

The final context dict is structured into named sections so the system prompt
can reference ``DỮ LIỆU DỰ ĐOÁN``, ``HÀNH VI HỌC TẬP``, and
``HƯỚNG DẪN CỐ VẤN`` explicitly.

Token budget
~~~~~~~~~~~~
``LLM_MAX_INPUT_TOKENS`` defaults to 8 000.  The system prompt, guidelines,
and conversation history all compete for that budget.  This module truncates
the activity and guideline sections when the total would exceed the budget,
keeping prediction data (smallest, most critical) intact.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from learnguard_backend.services.briefs import build_context
from learnguard_backend.services.student_activity import (
    build_student_activity,
    resolve_student_id,
)

logger = logging.getLogger(__name__)

# --- Semantic Layer: Vietnamese translations for technical variables & scales --

FEATURE_SEMANTICS: dict[str, dict[str, str]] = {
    "n_clicks_before_cutoff": {
        "ten": "Tổng lượt tương tác học tập (VLE)",
        "don_vi": "lượt click",
        "mo_ta": "Tổng số lượt truy cập và thao tác với học liệu trên hệ thống LMS/VLE trước ngày cutoff (ngày 27).",
    },
    "click_trend_w3_w4": {
        "ten": "Xu hướng tương tác tuần 3-4 so với tuần 1-2",
        "don_vi": "% biến động",
        "mo_ta": "Mức độ tăng hoặc giảm tương tác VLE ở 2 tuần gần nhất so với 2 tuần đầu kỳ (số âm thể hiện sự sụt giảm tương tác rõ rệt).",
    },
    "n_submitted_before_cutoff": {
        "ten": "Số bài đánh giá đã nộp",
        "don_vi": "bài",
        "mo_ta": "Tổng số bài kiểm tra/TMA sinh viên đã nộp trước ngày cutoff.",
    },
    "avg_score_submitted": {
        "ten": "Điểm trung bình các bài đã nộp",
        "don_vi": "điểm (thang 0-100)",
        "mo_ta": "Điểm số trung bình cộng của các bài kiểm tra đã nộp (thang 0-100, dưới 40 là mức trượt).",
    },
    "missingindicator_avg_score_submitted": {
        "ten": "Chưa có điểm bài tập nào trước cutoff",
        "don_vi": "cờ đánh dấu (1: chưa có điểm, 0: đã có điểm)",
        "mo_ta": "Nếu bằng 1, sinh viên chưa có bất kỳ bài kiểm tra nào được ghi nhận điểm trước ngày cutoff.",
    },
    "num_of_prev_attempts": {
        "ten": "Số lần đã học lại môn này",
        "don_vi": "lần",
        "mo_ta": "Số lần sinh viên đã từng đăng ký môn học này ở các kỳ trước nhưng chưa vượt qua.",
    },
    "studied_credits": {
        "ten": "Số tín chỉ đang đăng ký trong kỳ",
        "don_vi": "tín chỉ",
        "mo_ta": "Tổng số tín chỉ học tập mà sinh viên đăng ký trong kỳ học hiện tại (khối lượng học tập).",
    },
    "highest_education": {
        "ten": "Trình độ học vấn cao nhất",
        "don_vi": "phân loại",
        "mo_ta": "Bằng cấp hoặc trình độ học vấn đầu vào (HE Qualification, A Level, Lower than A Level).",
    },
    "imd_band": {
        "ten": "Chỉ số hoàn cảnh kinh tế - xã hội khu vực (IMD Band)",
        "don_vi": "khoảng % (0-10% đến 90-100%)",
        "mo_ta": "Mức độ khó khăn kinh tế - xã hội khu vực sinh sống (0-10% là khó khăn nhất, 90-100% là thuận lợi nhất).",
    },
    "age_band": {
        "ten": "Độ tuổi sinh viên",
        "don_vi": "nhóm tuổi",
        "mo_ta": "Nhóm tuổi khi tham gia khóa học (0-35, 35-55, 55<=).",
    },
    "gender": {
        "ten": "Giới tính",
        "don_vi": "phân loại",
        "mo_ta": "Giới tính của sinh viên (M: Nam, F: Nữ).",
    },
    "region": {
        "ten": "Khu vực sinh sống",
        "don_vi": "vùng",
        "mo_ta": "Khu vực địa lý nơi sinh viên đăng ký cư trú.",
    },
    "disability": {
        "ten": "Tình trạng khuyết tật",
        "don_vi": "trạng thái",
        "mo_ta": "Sinh viên có khai báo khuyết tật cần hỗ trợ học vụ hay không (Y: Có, N: Không).",
    },
    "date_registration": {
        "ten": "Thời điểm đăng ký môn học",
        "don_vi": "ngày (so với ngày khai giảng)",
        "mo_ta": "Số ngày đăng ký môn học so với ngày mở môn (số âm là đăng ký trước ngày khai giảng).",
    },
    "date_registration_gap": {
        "ten": "Khoảng cách ngày đăng ký môn",
        "don_vi": "ngày",
        "mo_ta": "Khoảng cách ngày đăng ký so với ngày mở khóa học.",
    },
    "missed_deadlines": {
        "ten": "Số bài nộp trễ hạn / quá hạn",
        "don_vi": "bài",
        "mo_ta": "Số lượng bài kiểm tra sinh viên nộp sau ngày hạn quy định hoặc chưa nộp khi đã qua hạn.",
    },
}


def resolve_feature_semantics(name: str) -> dict[str, str]:
    """Resolve a technical feature name (including prefixes) into human-readable Vietnamese semantics."""
    clean_name = str(name).strip()
    if clean_name in FEATURE_SEMANTICS:
        return FEATURE_SEMANTICS[clean_name]

    if "__" in clean_name:
        suffix = clean_name.split("__")[-1]
        if suffix in FEATURE_SEMANTICS:
            return FEATURE_SEMANTICS[suffix]

    match = re.search(r"clicks_week(\d+)", clean_name)
    if match:
        week_num = match.group(1)
        return {
            "ten": f"Lượt tương tác VLE tuần {week_num}",
            "don_vi": "lượt click",
            "mo_ta": f"Tổng số lượt click tương tác trên hệ thống học tập VLE trong tuần {week_num}.",
        }

    if "avg_score" in clean_name or "score" in clean_name:
        return FEATURE_SEMANTICS["avg_score_submitted"]

    return {
        "ten": clean_name.replace("_", " ").title(),
        "don_vi": "chỉ số",
        "mo_ta": f"Chỉ số học tập kỹ thuật: {clean_name}.",
    }


def apply_semantic_layer(prediction_ctx: dict[str, Any]) -> dict[str, Any]:
    """Translate raw technical variables, metrics, and SHAP signals into a semantic Vietnamese representation."""
    if not isinstance(prediction_ctx, dict):
        return prediction_ctx

    if "y_nghia_thang_diem_va_chi_so" in prediction_ctx:
        return prediction_ctx

    result = dict(prediction_ctx)

    risk_score = result.get("risk_score")
    threshold = result.get("threshold", 0.5)
    uncertainty = result.get("uncertainty")
    status = result.get("status", "scored")
    review_reason = result.get("review_reason")

    thresh_val = threshold if threshold is not None else 0.5
    is_high_risk = (risk_score is not None) and (risk_score >= thresh_val)

    if status == "manual_review":
        danh_gia_risk = (
            "CẦN XEM XÉT THỦ CÔNG (Độ bất định cao - Hệ thống chỉ đưa ra quan điểm tham khảo, KHÔNG tự động đánh giá/kết luận)"
        )
        giai_thich_risk = (
            f"Mô hình ghi nhận risk_score là {risk_score} (ngưỡng {thresh_val}), nhưng độ bất định cao (uncertainty = {uncertainty}). "
            "Theo quy định, hệ thống KHÔNG được tự động đánh giá hay kết luận dán nhãn sinh viên, "
            "mà CHỈ ĐƯỢC ĐƯA RA QUAN ĐIỂM / GÓC NHÌN THAM KHẢO để Cố vấn học tập tự xem xét và đánh giá thủ công."
        )
        giai_thich_status = (
            "manual_review: Độ bất định của mô hình cao (uncertainty >= 0.8) hoặc tín hiệu mâu thuẫn. "
            "Hệ thống KHÔNG tự động kết luận rủi ro thay cho người dùng, mà chỉ cung cấp góc nhìn gợi ý để Cố vấn tự đánh giá thủ công."
        )
    elif status == "insufficient_data":
        danh_gia_risk = (
            "THIẾU DỮ LIỆU ĐỂ ĐÁNH GIÁ (Dự đoán ngoại suy, không sử dụng làm kết luận rủi ro)"
        )
        giai_thich_risk = (
            f"Sinh viên bị thiếu dữ liệu hành vi thực tế (chưa có tương tác VLE hoặc chưa có bài kiểm tra nộp). "
            f"Điểm risk_score ({risk_score}) chỉ là giá trị ngoại suy kỹ thuật trên dữ liệu điền khuyết (imputed data). "
            "TUYỆT ĐỐI KHÔNG dùng điểm số này để tự động kết luận sinh viên có rủi ro hay an toàn."
        )
        giai_thich_status = (
            "insufficient_data: Sinh viên bị đánh tag thiếu dữ liệu học tập đầu vào "
            "(ví dụ: đăng ký muộn, không có tương tác VLE, hoặc chưa có bài kiểm tra). "
            "Mô hình không thể dự đoán chính xác; Cố vấn cần kiểm tra dữ liệu thực tế hoặc liên hệ trực tiếp sinh viên."
        )
    elif status == "no_prediction":
        danh_gia_risk = "CHƯA CÓ DỰ ĐOÁN TỪ MÔ HÌNH"
        giai_thich_risk = "Chưa có kết quả dự báo điểm rủi ro từ mô hình Machine Learning cho sinh viên này."
        giai_thich_status = (
            "no_prediction: Mô hình Machine Learning chưa/không thể chạy dự đoán cho hồ sơ này. "
            "Cố vấn cần theo dõi tiến độ học tập qua các kênh quản lý học vụ thông thường."
        )
    else:  # "scored" or other
        danh_gia_risk = "NGUY CƠ RỚT MÔN CAO (Cần can thiệp)" if is_high_risk else "NGUY CƠ THẤP / AN TOÀN"
        giai_thich_risk = (
            f"Xác suất sinh viên có nguy cơ rớt môn được dự báo bởi mô hình AI (dựa trên 4 tuần đầu kỳ). "
            f"Giá trị hiện tại là {risk_score} (ngưỡng phân loại là {thresh_val}). "
            f"{'Vượt ngưỡng -> Sinh viên cần được cố vấn hỗ trợ sớm.' if is_high_risk else 'Dưới ngưỡng -> Sinh viên đang tiến độ tốt.'}"
        )
        giai_thich_status = "scored: mô hình đã chấm điểm dự báo tự động thành công với độ tin cậy tốt."

    # 1. Thang điểm và ý nghĩa các chỉ số mô hình
    thang_diem_chi_so: dict[str, Any] = {
        "diem_nguy_co (risk_score)": {
            "gia_tri": risk_score,
            "thang_do": "0.00 đến 1.00 (tương đương 0% đến 100%)",
            "danh_gia": danh_gia_risk,
            "giai_thich": giai_thich_risk,
        },
        "nguong_quyet_dinh (threshold)": {
            "gia_tri": thresh_val,
            "giai_thich": f"Ngưỡng phân loại rủi ro của hệ thống (mặc định {thresh_val}). Nếu risk_score >= {thresh_val}: kích hoạt cảnh báo can thiệp.",
        },
        "do_bat_dinh (uncertainty)": {
            "gia_tri": uncertainty,
            "thang_do": "0.00 đến 1.00",
            "giai_thich": (
                "Độ không chắc chắn của mô hình. Giá trị càng thấp độ tin cậy càng cao. "
                "Nếu >= 0.80 hoặc hồ sơ ở trạng thái manual_review, hệ thống chỉ nêu quan điểm tham khảo để cố vấn tự xem xét thủ công."
            ),
        },
        "trang_thai_ho_so (status)": {
            "gia_tri": status,
            "giai_thich": giai_thich_status,
        },
        "quy_tac_y_nghia_SHAP": {
            "contribution_duong (>0)": "Đặc trưng LÀM TĂNG NGUY CƠ RỚT MÔN (nguyên nhân gây rủi ro chính cần can thiệp).",
            "contribution_am (<0)": "Đặc trưng GIÚP GIẢM NGUY CƠ RỚT MÔN (điểm mạnh/điểm sáng tích cực của sinh viên).",
        },
    }

    if review_reason:
        reason_explanation = (
            "Thiếu dữ liệu hành vi học tập đầu vào."
            if review_reason == "insufficient_data"
            else "Độ bất định mô hình cao (uncertainty >= 0.8)."
            if review_reason == "high_uncertainty"
            else "Chưa có dự đoán từ mô hình."
            if review_reason == "no_prediction"
            else f"Lý do rà soát: {review_reason}."
        )
        thang_diem_chi_so["ly_do_ra_soat (review_reason)"] = {
            "gia_tri": review_reason,
            "giai_thich": reason_explanation,
        }

    result["y_nghia_thang_diem_va_chi_so"] = thang_diem_chi_so

    # 2. Dịch nghĩa các tín hiệu SHAP (top_signals)
    raw_signals = result.get("top_signals", [])
    semantic_signals: list[dict[str, Any]] = []
    for sig in raw_signals:
        if not isinstance(sig, dict):
            continue
        feat_name = sig.get("feature_name", "")
        sem = resolve_feature_semantics(feat_name)
        contrib = sig.get("contribution")
        direction = sig.get("direction")
        is_risk_factor = (contrib is not None and contrib > 0) or direction == "increases_risk"

        obs_val = sig.get("observed_value")
        don_vi = sem.get("don_vi", "")
        val_str = f"{obs_val} {don_vi}" if obs_val is not None else "Chưa có dữ liệu"

        semantic_signals.append({
            "ten_bien_ky_thuat": feat_name,
            "ten_tieng_viet": sem.get("ten", feat_name),
            "gia_tri_thuc_te": val_str,
            "muc_do_anh_huong (SHAP)": f"{contrib:+.3f}" if isinstance(contrib, (int, float)) else None,
            "chieu_tac_dong": "LÀM TĂNG NGUY CƠ RỚT (Lý do rủi ro)" if is_risk_factor else "ĐIỂM TÍCH CỰC (Giúp giảm rủi ro)",
            "mo_ta_bien": sem.get("mo_ta", ""),
        })
    result["tin_hieu_dong_gop_chinh (top_signals_tieng_viet)"] = semantic_signals

    # 3. Dịch nghĩa toàn bộ đặc trưng (features)
    raw_features = result.get("features", {})
    if isinstance(raw_features, dict):
        semantic_features: dict[str, Any] = {}
        for fname, fval in raw_features.items():
            sem = resolve_feature_semantics(fname)
            semantic_features[sem.get("ten", fname)] = {
                "ten_bien_goc": fname,
                "gia_tri": fval,
                "don_vi": sem.get("don_vi", ""),
                "mo_ta": sem.get("mo_ta", ""),
            }
        result["thong_ke_hoc_tap_chi_tiet (features_tieng_viet)"] = semantic_features

    return result

# --- Advisor guidelines (loaded once, parsed into sections, cached) -------

_GUIDELINES_PATH = Path(__file__).with_name("advisor_guidelines.md")
_guidelines_cache: str | None = None
_guidelines_sections_cache: dict[str, str] | None = None

# Section keys that map to heading patterns in advisor_guidelines.md.
# Each key identifies a logical section of the handbook.
_SECTION_HEADINGS: list[tuple[str, str]] = [
    ("risk_classification", "## 1. Phân Loại Mức Độ Rủi Ro"),
    ("metrics_explanation", "## 2. Giải Thích Các Chỉ Số Chính"),
    ("communication",       "## 3. Hướng Dẫn Giao Tiếp"),
    ("escalation",          "## 4. Quy Trình Escalation"),
    ("important_notes",     "## 5. Lưu Ý Quan Trọng"),
    ("crisis",              "## 6. Xử Lý Tình Huống Khẩn Cấp"),
]

# Which guideline sections to inject for each chat intent.
# Empty list = no guidelines needed → saves all guideline tokens.
INTENT_TO_SECTIONS: dict[str, list[str]] = {
    "why_risk":          [],  # Data-only answer, no guidelines needed
    "casual":            [],  # Greeting/small-talk, no guidelines needed
    "intervention":      ["risk_classification", "escalation", "crisis"],
    "draft_email":       ["communication"],
    "general_analysis":  ["metrics_explanation", "important_notes"],
}


def _parse_sections(full_text: str) -> dict[str, str]:
    """Split the full guidelines markdown into named sections by ## headings.

    Each section spans from its heading to the next heading (or EOF).
    """
    sections: dict[str, str] = {}
    # Build (key, start_index) pairs for sections found in text
    positions: list[tuple[str, int]] = []
    for key, heading in _SECTION_HEADINGS:
        idx = full_text.find(heading)
        if idx != -1:
            positions.append((key, idx))

    # Sort by position in document
    positions.sort(key=lambda x: x[1])

    for i, (key, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(full_text)
        section_text = full_text[start:end].strip()
        if section_text:
            sections[key] = section_text

    return sections


def _load_guidelines(path: Path | None = None) -> str:
    """Load the advisor handbook from disk (cached after first call).

    Returns an empty string when the file is missing — the chat still works,
    just without the guideline section.
    """
    global _guidelines_cache
    if _guidelines_cache is not None:
        return _guidelines_cache

    target = path or _GUIDELINES_PATH
    try:
        text = target.read_text(encoding="utf-8")
        _guidelines_cache = text
        return text
    except OSError:
        logger.warning("Advisor guidelines file not found at %s", target)
        _guidelines_cache = ""
        return ""


def _load_guideline_sections(path: Path | None = None) -> dict[str, str]:
    """Load and parse guidelines into named sections (cached)."""
    global _guidelines_sections_cache
    if _guidelines_sections_cache is not None:
        return _guidelines_sections_cache

    full_text = _load_guidelines(path)
    if not full_text:
        _guidelines_sections_cache = {}
        return _guidelines_sections_cache

    _guidelines_sections_cache = _parse_sections(full_text)
    logger.info(
        "Parsed advisor guidelines into %d sections: %s",
        len(_guidelines_sections_cache),
        list(_guidelines_sections_cache.keys()),
    )
    return _guidelines_sections_cache


def select_guidelines_for_intent(intent: str, path: Path | None = None) -> str:
    """Return only the guideline sections relevant to the given intent.

    This is the core of the Intent-based Section Loading optimisation:
    instead of injecting the full ~12 KB handbook into every LLM context,
    we select only the 1-3 sections that match the advisor's intent,
    cutting guideline tokens by 70-80%.
    """
    section_keys = INTENT_TO_SECTIONS.get(intent, [])
    if not section_keys:
        return ""

    sections = _load_guideline_sections(path)
    parts = [sections[k] for k in section_keys if k in sections]
    return "\n\n---\n\n".join(parts) if parts else ""


def reset_guidelines_cache() -> None:
    """Reset the cached guidelines (for tests or hot-reload)."""
    global _guidelines_cache, _guidelines_sections_cache
    _guidelines_cache = None
    _guidelines_sections_cache = None


# --- Token budget helpers ------------------------------------------------

# Rough estimate: 1 token ≈ 4 chars for Vietnamese/mixed text.
_CHARS_PER_TOKEN = 4

# Reserve tokens for system prompt template + conversation history.
_RESERVED_TOKENS = 3000

# Minimum tokens allocated to guidelines (truncated to fit if needed).
_MIN_GUIDELINES_TOKENS = 500


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (conservative, errs on the high side)."""
    return len(text) // _CHARS_PER_TOKEN + 1


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate ``text`` to approximately ``max_tokens``."""
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    # Try to cut at a paragraph boundary.
    cut = text[:max_chars].rfind("\n\n")
    if cut > max_chars // 2:
        return text[:cut] + "\n\n[... nội dung hướng dẫn đã được lược bớt ...]"
    return text[:max_chars] + "\n[... lược bớt ...]"


# --- Activity data formatting --------------------------------------------

def _format_activity_summary(activity: dict[str, Any]) -> dict[str, Any]:
    """Distill activity data into a compact, LLM-friendly summary.

    Instead of dumping the full raw dict (which can be very large for
    students with many submissions), this produces a compact representation
    that fits within the token budget while preserving all actionable info.
    """
    weeks = activity.get("weeks", [])
    submissions = activity.get("submissions", [])
    breakdown = activity.get("activity_breakdown", [])

    # Weekly VLE summary
    week_summary = []
    for w in weeks:
        week_summary.append({
            "tuần": w.get("label", ""),
            "tổng_click": w.get("clicks", 0),
            "số_ngày_hoạt_động": w.get("days_active", 0),
        })

    # Compute trends
    click_values = [w.get("clicks", 0) for w in weeks]
    total_clicks = sum(click_values)
    # Compare last 2 weeks vs first 2 weeks (excluding pre-start)
    if len(click_values) >= 5:  # pre-start + 4 weeks
        early = click_values[1] + click_values[2]  # week 1+2
        late = click_values[3] + click_values[4]    # week 3+4
        if early > 0:
            trend_pct = round((late - early) / early * 100, 1)
        else:
            trend_pct = 100.0 if late > 0 else 0.0
    else:
        trend_pct = None

    # Submissions summary
    submitted_count = sum(1 for s in submissions if s.get("status") == "submitted")
    not_submitted_count = sum(1 for s in submissions if s.get("status") == "not_submitted")
    scores = [s["score"] for s in submissions if s.get("score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    # Late submissions
    late_submissions = []
    for s in submissions:
        if (
            s.get("status") == "submitted"
            and s.get("submitted_day") is not None
            and s.get("assessment_day") is not None
            and s["submitted_day"] > s["assessment_day"]
        ):
            late_submissions.append({
                "loại": s.get("assessment_type", ""),
                "trễ_ngày": s["submitted_day"] - s["assessment_day"],
            })

    # Activity breakdown (top categories)
    breakdown_summary = [
        {"loại_tài_nguyên": b["category"], "click": b["clicks"]}
        for b in breakdown
    ]

    result: dict[str, Any] = {
        "cutoff_day": activity.get("cutoff_day", 27),
        "tổng_click_VLE": total_clicks,
        "hoạt_động_theo_tuần": week_summary,
        "bài_nộp": {
            "đã_nộp": submitted_count,
            "chưa_nộp": not_submitted_count,
            "điểm_TB": avg_score,
        },
        "phân_loại_hoạt_động": breakdown_summary,
    }
    if trend_pct is not None:
        result["xu_hướng_click_tuần3_4_vs_1_2"] = f"{trend_pct:+.1f}%"
    if late_submissions:
        result["bài_nộp_trễ"] = late_submissions
    # Include full submission details for precise citation
    if submissions:
        result["chi_tiết_bài_nộp"] = [
            {
                "loại": s.get("assessment_type", ""),
                "ngày_hạn": s.get("assessment_day"),
                "ngày_nộp": s.get("submitted_day"),
                "điểm": s.get("score"),
                "trọng_số": s.get("weight"),
                "trạng_thái": "đã nộp" if s.get("status") == "submitted" else "chưa nộp",
            }
            for s in submissions
        ]
    return result


# --- Main context builder ------------------------------------------------

def build_chat_context(
    prediction_record: Any,
    *,
    data_dir: Path | None = None,
    guidelines_path: Path | None = None,
    max_input_tokens: int = 8000,
    intent: str | None = None,
) -> dict[str, Any]:
    """Build a comprehensive, PII-free context dict for the chat LLM.

    Parameters
    ----------
    prediction_record
        An ``ml_predictions`` row (SQLAlchemy entity or dataclass record).
    data_dir
        Path to raw OULAD CSVs.  ``None`` → activity data is omitted.
    guidelines_path
        Override path for ``advisor_guidelines.md``.  ``None`` → default.
    max_input_tokens
        Total token budget for the entire LLM input.
    intent
        Chat intent (from ``classify_message_intent``).  When provided,
        only the guideline sections relevant to this intent are loaded
        instead of the full handbook, cutting guideline tokens by 70-80%.
        ``None`` falls back to loading the full file (legacy behaviour).

    Returns
    -------
    dict with keys:
        - ``prediction`` — output of ``build_context(prediction_record)``
        - ``activity`` — formatted student activity summary (or ``None``)
        - ``guidelines`` — advisor handbook text (possibly truncated)
        - ``activity_available`` — bool, whether activity data was loaded
        - ``guidelines_available`` — bool
    """
    # 1. Prediction context (always available, enriched with Semantic Layer)
    prediction_raw = build_context(prediction_record)
    prediction_ctx = apply_semantic_layer(prediction_raw)

    # 2. Student activity (best-effort: may fail or be unconfigured)
    activity_summary: dict[str, Any] | None = None
    activity_available = False

    if data_dir is not None:
        try:
            id_student = resolve_student_id(
                data_dir,
                prediction_record.code_module,
                prediction_record.code_presentation,
                prediction_record.student_key,
            )
            if id_student is not None:
                raw_activity = build_student_activity(
                    data_dir,
                    prediction_record.code_module,
                    prediction_record.code_presentation,
                    id_student,
                )
                activity_summary = _format_activity_summary(raw_activity)
                activity_available = True
            else:
                logger.warning(
                    "Could not resolve student_key=%s to id_student",
                    prediction_record.student_key,
                )
        except Exception:
            logger.exception(
                "Failed to load activity data for student_key=%s",
                prediction_record.student_key,
            )

    # 3. Advisor guidelines — intent-based section loading
    if intent is not None:
        # New path: load only the sections relevant to this intent.
        raw_guidelines = select_guidelines_for_intent(intent, guidelines_path)
    else:
        # Legacy path: load the full file (for callers that don't pass intent).
        raw_guidelines = _load_guidelines(guidelines_path)
    guidelines_available = bool(raw_guidelines)

    # 4. Token budget management
    prediction_json = json.dumps(prediction_ctx, ensure_ascii=False, default=str)
    prediction_tokens = _estimate_tokens(prediction_json)

    activity_json = ""
    activity_tokens = 0
    if activity_summary is not None:
        activity_json = json.dumps(activity_summary, ensure_ascii=False, default=str)
        activity_tokens = _estimate_tokens(activity_json)

    remaining = max_input_tokens - _RESERVED_TOKENS - prediction_tokens - activity_tokens
    if remaining < 0:
        # Activity data too large → truncate submission details
        if activity_summary is not None and "chi_tiết_bài_nộp" in activity_summary:
            del activity_summary["chi_tiết_bài_nộp"]
            activity_json = json.dumps(activity_summary, ensure_ascii=False, default=str)
            activity_tokens = _estimate_tokens(activity_json)
            remaining = max_input_tokens - _RESERVED_TOKENS - prediction_tokens - activity_tokens

    guidelines_tokens = _estimate_tokens(raw_guidelines) if raw_guidelines else 0
    if guidelines_tokens > 0 and guidelines_tokens > remaining:
        # Truncate guidelines to fit
        guideline_budget = max(remaining, _MIN_GUIDELINES_TOKENS)
        raw_guidelines = _truncate_to_tokens(raw_guidelines, guideline_budget)

    return {
        "prediction": prediction_ctx,
        "activity": activity_summary,
        "guidelines": raw_guidelines if raw_guidelines else None,
        "activity_available": activity_available,
        "guidelines_available": guidelines_available,
    }


def prune_context_for_intent(context: dict[str, Any], intent: str = "general_analysis") -> dict[str, Any]:
    """Prune context based on question intent to prevent LLM from hallucinating/over-answering.

    - 'why_risk': Omit guidelines, and prune top_signals to only keep risk factors (contribution > 0).
    - 'casual': Omit guidelines and detailed activity.
    - 'intervention': Keep only intervention-related guideline sections.
    - 'draft_email': Keep only email template guideline sections.
    - 'general_analysis': Keep only metrics explanation guideline sections.

    When guidelines were loaded as the full file (legacy callers that don't
    pass ``intent`` to ``build_chat_context``), this function replaces them
    with only the sections relevant to the current intent — achieving the
    same token savings retroactively.
    """
    pruned = dict(context)

    # 1. Prediction data pruning
    pred = dict(pruned.get("prediction", pruned))
    if pred and isinstance(pred, dict):
        if intent == "why_risk":
            # Only keep risk factors (increases_risk or contribution > 0)
            raw_signals = pred.get("top_signals", [])
            risk_signals = [
                s for s in raw_signals
                if isinstance(s, dict) and ((s.get("contribution") or 0) > 0 or s.get("direction") == "increases_risk")
            ]
            pred["top_signals"] = risk_signals

            # Filter semantic signals if already present
            if "tin_hieu_dong_gop_chinh (top_signals_tieng_viet)" in pred:
                pred["tin_hieu_dong_gop_chinh (top_signals_tieng_viet)"] = [
                    s for s in pred["tin_hieu_dong_gop_chinh (top_signals_tieng_viet)"]
                    if "TĂNG NGUY CƠ" in s.get("chieu_tac_dong", "")
                ]

        if "prediction" in pruned:
            pruned["prediction"] = pred
        else:
            pruned = pred

    # 2. Guidelines pruning: replace full guidelines with intent-relevant sections
    section_keys = INTENT_TO_SECTIONS.get(intent, [])
    if not section_keys:
        # Intent needs no guidelines at all (why_risk, casual)
        pruned["guidelines"] = None
    elif pruned.get("guidelines"):
        guidelines_text = str(pruned["guidelines"])
        has_handbook_markers = any(heading in guidelines_text for _, heading in _SECTION_HEADINGS)
        if has_handbook_markers:
            # Parse sections directly from the provided text
            sections = _parse_sections(guidelines_text)
            if sections:
                parts = [sections[k] for k in section_keys if k in sections]
                if parts:
                    pruned["guidelines"] = "\n\n---\n\n".join(parts)

    # 3. Activity pruning: Hide detailed logs in casual greetings
    if intent == "casual":
        pruned["activity"] = None

    return pruned


def format_system_message(
    context: dict[str, Any],
    system_prompt: str,
    mode_instruction: str = "",
    intent: str = "general_analysis",
) -> str:
    """Assemble the final system message from the enriched context.

    Structures the content into clearly delimited sections so the LLM
    can reference each data source by name.
    """
    pruned_context = prune_context_for_intent(context, intent)
    parts = [system_prompt]
    if mode_instruction:
        parts.append(f"\n\n{mode_instruction}")

    # Section 1: Prediction data with Semantic Layer (always present)
    pred_data = pruned_context.get("prediction", pruned_context)
    pred_with_semantics = apply_semantic_layer(pred_data)
    prediction_json = json.dumps(
        pred_with_semantics, ensure_ascii=False, default=str
    )
    parts.append(
        f"\n\n━━━ DỮ LIỆU DỰ ĐOÁN (JSON) ━━━\n{prediction_json}"
    )

    # Section 2: Student activity (if available)
    if pruned_context.get("activity"):
        activity_json = json.dumps(
            pruned_context["activity"], ensure_ascii=False, default=str
        )
        parts.append(
            f"\n\n━━━ HÀNH VI HỌC TẬP (JSON) ━━━\n{activity_json}"
        )
    elif intent != "casual":
        parts.append(
            "\n\n━━━ HÀNH VI HỌC TẬP ━━━\n"
            "[Không có dữ liệu hoạt động chi tiết — OULAD_DATA_DIR chưa được cấu hình "
            "hoặc không tìm thấy sinh viên trong CSV gốc. "
            "Chỉ sử dụng dữ liệu dự đoán ở trên để trả lời.]"
        )

    # Section 3: Advisor guidelines (if available)
    if pruned_context.get("guidelines"):
        parts.append(
            f"\n\n━━━ HƯỚNG DẪN CỐ VẤN ━━━\n{pruned_context['guidelines']}"
        )

    return "".join(parts)
