"""M7 entity contracts. TODO: map these records to SQLAlchemy/PostgreSQL."""

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
class ModelVersionRecord:
    model_version: str
    algorithm: str
    config: dict[str, Any]
    feature_list_hash: str
    metrics: dict[str, Any]
    threshold: float
    registered_at: datetime | None = None


@dataclass
class PredictionRecord:
    prediction_id: int | None
    student_key: str
    code_module: str
    code_presentation: str
    risk_score: float | None
    predicted_label: int | None
    uncertainty: float | None
    status: str
    model_version: str
    snapshot_version: str
    cutoff_day: int
    top_signals: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BriefRecord:
    brief_id: int | None
    prediction_id: int
    version: int
    content: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)
    status: str = "awaiting_review"


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
    payload: dict[str, Any] = field(default_factory=dict)
