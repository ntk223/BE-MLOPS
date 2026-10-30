"""M7 entity DTOs aligned with DB_CONTRACT.md v1.0 rev2.

MLPredictionRecord mirrors the pipeline-owned ``ml_predictions`` table
(contract: ml-data/docs/M5_PREDICTION_BATCH.md §4) — read-only for the
backend. Other records map to backend-owned tables.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class UserRecord:
    user_id: int | None
    email: str
    full_name: str
    role: str  # advisor | admin
    password_hash: str
    active: bool = True


@dataclass
class MLPredictionRecord:
    """One row of ``ml_predictions`` — always the latest run (upsert)."""

    code_module: str
    code_presentation: str
    student_key: str
    split: str
    risk_score: float | None
    predicted_label: int | None  # NULL khi không gán nhãn được
    threshold: float | None
    uncertainty: float | None
    status: str  # scored | manual_review | insufficient_data | no_prediction
    review_reason: str | None
    model_version: str
    snapshot_version: str
    cutoff_day: int
    top_signals_json: str | None
    run_id: str
    scored_at: datetime | None = None


@dataclass
class BriefRecord:
    brief_id: int | None
    code_module: str
    code_presentation: str
    student_key: str
    version: int
    content: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)
    status: str = "awaiting_review"
    created_by: int | None = None


@dataclass
class DecisionRecord:
    decision_id: int | None
    brief_id: int
    user_id: int
    action: str  # accept | reject | edit
    feedback: str | None = None


@dataclass
class LLMCallRecord:
    call_id: int | None
    brief_id: int | None
    model: str
    latency_ms: int
    status: str
    cost_usd: float | None = None


@dataclass
class AuditLogRecord:
    log_id: int | None
    user_id: int | None
    action: str
    entity: str
    entity_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
