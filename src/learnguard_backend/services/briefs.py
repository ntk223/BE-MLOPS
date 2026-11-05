"""M8 advisor brief generation contracts.

The brief is written for the ADVISOR (never sent to students — README
boundary). The LLM only receives a minimal, PII-free context (hashed
``student_key`` + aggregate behavioural/academic features, no id_student)
and every reason must cite evidence features that were actually supplied.
Ungrounded citations are removed and reported in ``limitations``. Any LLM
failure degrades to an explicit ``fallback_review`` / ``unavailable`` state
instead of fabricating content (status mapping: LLM OK + clean citations ->
``generated``).
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from learnguard_backend.services.llm import LLMService

STATUS_GENERATED = "generated"
STATUS_FALLBACK_REVIEW = "fallback_review"
STATUS_UNAVAILABLE = "unavailable"

BRIEF_SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích học vụ của LearnGuard, viết brief tiếng Việt cho "
    "CỐ VẤN học tập (brief KHÔNG BAO GIỜ được gửi trực tiếp cho sinh viên).\n"
    "Quy tắc bắt buộc:\n"
    "1. Chỉ sử dụng dữ liệu có trong phần DỮ LIỆU bên dưới. Không bịa số liệu, "
    "không suy đoán ngoài dữ liệu.\n"
    "2. Mỗi reason phải trích dẫn ít nhất một feature nằm trong DỮ LIỆU "
    "(ghi trong evidence_features); không được dùng feature nào khác.\n"
    "3. Không nhắc đến bất kỳ định danh cá nhân nào; chỉ được dùng student_key "
    "đã băm nếu cần.\n"
    "4. Trả về DUY NHẤT một đối tượng JSON hợp lệ, không kèm markdown hay giải "
    "thích, theo đúng schema:\n"
    '{"summary": str, "reasons": [{"text": str, "evidence_features": [str]}], '
    '"support_suggestions": [str], "limitations": [str]}'
)


@dataclass(frozen=True)
class BriefEvidence:
    feature_name: str
    observed_value: Any
    contribution: float | None = None


@dataclass
class BriefReason:
    text: str
    evidence_features: list[str] = field(default_factory=list)


@dataclass
class AdvisorBriefDraft:
    summary: str
    reasons: list[BriefReason]
    evidence: list[BriefEvidence]
    support_template_ids: list[str]
    limitations: list[str] = field(default_factory=list)


@dataclass
class BriefGenerationResult:
    status: str  # generated | fallback_review | unavailable
    draft: AdvisorBriefDraft | None
    model_name: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    error_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class BriefProvider(Protocol):
    def generate(self, context: dict[str, Any]) -> AdvisorBriefDraft: ...


def _parse_json_object(raw: str | None) -> dict[str, Any] | None:
    """Defensively parse a JSON object out of an LLM text response."""
    if not raw:
        return None
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _observed_for(feature_name: str, features: dict[str, Any]) -> Any:
    """Map a pipeline signal name (e.g. ``clicks__n_clicks_before_cutoff`` or
    ``score__missingindicator_avg_score_submitted``) to its raw feature value
    via prefix/suffix matching against the available feature keys."""
    if feature_name in features:
        return features[feature_name]
    tail = feature_name.split("__")[-1]
    if tail in features:
        return features[tail]
    for key in sorted(features, key=len, reverse=True):
        if feature_name.endswith(key):
            return features[key]
    return None


def build_context(record: Any) -> dict[str, Any]:
    """Build the minimal PII-free LLM context from an ``ml_predictions`` row.

    Only the hashed ``student_key`` leaves the backend — never id_student or
    any direct identifier (DB_CONTRACT.md PII policy).
    """
    features: dict[str, Any] = {}
    try:
        raw_features = json.loads(record.features_json) if record.features_json else {}
    except json.JSONDecodeError:
        raw_features = {}
    if isinstance(raw_features, dict):
        features = {
            name: value
            for name, value in raw_features.items()
            if isinstance(name, str)
        }

    top_signals: list[dict[str, Any]] = []
    try:
        raw_signals = json.loads(record.top_signals_json) if record.top_signals_json else []
    except json.JSONDecodeError:
        raw_signals = []
    for signal in raw_signals if isinstance(raw_signals, list) else []:
        if not isinstance(signal, dict):
            continue
        name = signal.get("feature_name")
        if not isinstance(name, str):
            continue
        contribution = signal.get("contribution")
        top_signals.append(
            {
                "feature_name": name,
                "contribution": contribution if isinstance(contribution, (int, float)) else None,
                "direction": signal.get("direction"),
                "observed_value": _observed_for(name, features),
            }
        )

    return {
        "student_key": record.student_key,
        "status": record.status,
        "risk_score": record.risk_score,
        "threshold": record.threshold,
        "uncertainty": record.uncertainty,
        "cutoff_day": record.cutoff_day,
        "top_signals": top_signals,
        "features": features,
    }


class AdvisorBriefService:
    """Generates advisor brief drafts through the shared LLM service."""

    def __init__(self, llm: LLMService | Any | None = None) -> None:
        # ``llm`` may be any object exposing ``complete(messages, **options)``
        # returning an ``LLMResponse``; tests inject fakes here.
        self._llm = llm

    def _llm_service(self) -> Any:
        if self._llm is None:
            from learnguard_backend.services.llm import get_llm_service

            self._llm = get_llm_service()
        return self._llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prediction_id: int | str,
        context: dict[str, Any] | None = None,
    ) -> BriefGenerationResult:
        """Build the minimal context, call the LLM, validate, and fallback.

        Never raises: any LLM/config/parse failure returns an explicit
        ``fallback_review`` (or ``unavailable``) result without fabrication.
        """
        if not context:
            return self.make_fallback(prediction_id, "missing_prediction_context")

        allowed_evidence = self._evidence_from_context(context)
        llm_context = {
            key: value for key, value in context.items() if key != "allowed_evidence"
        }
        messages = [
            {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "DỮ LIỆU DỰ ĐOÁN (JSON, chỉ được dùng dữ liệu này):\n"
                    + json.dumps(llm_context, ensure_ascii=False, default=str)
                ),
            },
        ]

        try:
            llm = self._llm_service()
        except Exception as exc:  # noqa: BLE001 — degrade, never 500
            return self.make_fallback(
                prediction_id,
                f"llm_not_configured ({type(exc).__name__})",
                status=STATUS_UNAVAILABLE,
            )

        try:
            response = llm.complete(messages, temperature=0.2)
        except Exception as exc:  # noqa: BLE001 — degrade, never 500
            result = self.make_fallback(
                prediction_id, f"llm_error ({type(exc).__name__})"
            )
            result.model_name = getattr(llm, "model_name", None)
            return result

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        draft = self._parse_draft(getattr(response, "content", None))
        if draft is None:
            result = self.make_fallback(prediction_id, "llm_response_not_parseable")
            result.model_name = getattr(response, "model", None)
            result.latency_ms = getattr(response, "latency_ms", None)
            result.cost_usd = getattr(response, "cost_usd", None)
            result.prompt_tokens = prompt_tokens
            result.completion_tokens = completion_tokens
            return result

        draft.evidence = allowed_evidence
        ungrounded = self.validate_citations(draft, allowed_evidence)
        if ungrounded:
            before = len(draft.reasons)
            draft.reasons = [
                reason
                for reason in draft.reasons
                if not (set(reason.evidence_features) & set(ungrounded))
            ]
            draft.limitations.append(
                f"Đã gỡ {before - len(draft.reasons)} reason trích dẫn feature "
                "không có trong dữ liệu đầu vào: " + ", ".join(ungrounded)
            )

        return BriefGenerationResult(
            status=STATUS_GENERATED,
            draft=draft,
            model_name=getattr(response, "model", None),
            latency_ms=getattr(response, "latency_ms", None),
            cost_usd=getattr(response, "cost_usd", None),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def validate_citations(
        self, draft: AdvisorBriefDraft, allowed_evidence: list[BriefEvidence]
    ) -> list[str]:
        """Report feature names cited by reasons but absent from the evidence
        actually supplied to the LLM (ungrounded claims)."""
        allowed = {evidence.feature_name for evidence in allowed_evidence}
        cited: set[str] = set()
        for reason in draft.reasons:
            cited.update(
                name for name in reason.evidence_features if isinstance(name, str)
            )
        return sorted(cited - allowed)

    def make_fallback(
        self,
        prediction_id: int | str,
        reason: str,
        status: str = STATUS_FALLBACK_REVIEW,
    ) -> BriefGenerationResult:
        """Explicit fallback/unavailable state with no fabricated analysis."""
        draft = AdvisorBriefDraft(
            summary=(
                "Hệ thống chưa tạo được bản phân tích tự động cho sinh viên này. "
                "Vui lòng xem dữ liệu dự đoán (risk score, top signals) và tự "
                "đánh giá trước khi quyết định hỗ trợ."
            ),
            reasons=[],
            evidence=[],
            support_template_ids=[],
            limitations=[
                f"Không tạo được brief bằng LLM (lý do: {reason}).",
                "Nội dung dưới đây không có suy luận từ mô hình; advisor cần "
                "tự đánh giá dựa trên dữ liệu dự đoán.",
            ],
        )
        return BriefGenerationResult(status=status, draft=draft, error_reason=reason)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _evidence_from_context(context: dict[str, Any]) -> list[BriefEvidence]:
        """Every feature the LLM was actually given is citable evidence."""
        features = context.get("features") or {}
        evidence: list[BriefEvidence] = []
        seen: set[str] = set()
        for signal in context.get("top_signals") or []:
            name = signal.get("feature_name")
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            observed = signal.get("observed_value")
            if observed is None:
                observed = _observed_for(name, features)
            contribution = signal.get("contribution")
            evidence.append(
                BriefEvidence(
                    feature_name=name,
                    observed_value=observed,
                    contribution=(
                        contribution
                        if isinstance(contribution, (int, float))
                        and not isinstance(contribution, bool)
                        else None
                    ),
                )
            )
        for name, value in features.items():
            if name in seen:
                continue
            seen.add(name)
            evidence.append(BriefEvidence(feature_name=name, observed_value=value))
        return evidence

    def _parse_draft(self, content: Any) -> AdvisorBriefDraft | None:
        payload = content if isinstance(content, dict) else _parse_json_object(content)
        if payload is None:
            return None
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None

        reasons: list[BriefReason] = []
        raw_reasons = payload.get("reasons")
        for item in raw_reasons if isinstance(raw_reasons, list) else []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            raw_features = item.get("evidence_features")
            evidence_features = [
                name
                for name in (raw_features if isinstance(raw_features, list) else [])
                if isinstance(name, str)
            ]
            reasons.append(BriefReason(text=text, evidence_features=evidence_features))

        raw_support = payload.get("support_suggestions")
        support = [
            item
            for item in (raw_support if isinstance(raw_support, list) else [])
            if isinstance(item, str)
        ]
        raw_limitations = payload.get("limitations")
        limitations = [
            item
            for item in (raw_limitations if isinstance(raw_limitations, list) else [])
            if isinstance(item, str)
        ]

        return AdvisorBriefDraft(
            summary=summary,
            reasons=reasons,
            evidence=[],
            support_template_ids=support,
            limitations=limitations,
        )
