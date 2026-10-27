"""M8 advisor brief generation contracts."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class BriefEvidence:
    feature_name: str
    observed_value: Any
    contribution: float | None = None


@dataclass
class AdvisorBriefDraft:
    summary: str
    reasons: list[str]
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


class BriefProvider(Protocol):
    def generate(self, context: dict[str, Any]) -> AdvisorBriefDraft: ...


class AdvisorBriefService:
    def generate(self, prediction_id: int) -> BriefGenerationResult:
        """TODO: build minimal context, call provider, validate, and fallback."""
        raise NotImplementedError

    def validate_citations(self, draft: AdvisorBriefDraft,
                           allowed_evidence: list[BriefEvidence]) -> list[str]:
        """TODO: report claims not grounded in supplied evidence."""
        raise NotImplementedError

    def make_fallback(self, prediction_id: int,
                      reason: str) -> BriefGenerationResult:
        """TODO: return explicit fallback/unavailable state without fabrication."""
        raise NotImplementedError
