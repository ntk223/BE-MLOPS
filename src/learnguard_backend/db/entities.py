"""M7 SQLAlchemy entities per DB_CONTRACT.md v1.0 rev2 (read-only backend).

- ``MLPrediction`` maps the pipeline-owned ``ml_predictions`` table
  (contract: ml-data/docs/M5_PREDICTION_BATCH.md §4). Backend only SELECTs —
  no INSERT/UPDATE/DDL code path exists and none may be added.
- All other tables are backend-owned and managed via Alembic.
- The dataclass records in db/models.py stay as DTO contracts for services.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    CHAR,
    CheckConstraint,
    DOUBLE_PRECISION,
    ForeignKey,
    Index,
    INTEGER,
    NUMERIC,
    SMALLINT,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Enum domains (DB_CONTRACT.md §8; scoring.py is the source of truth) ------

PREDICTION_STATUSES = (
    "scored",
    "insufficient_data",
    "manual_review",
    "no_prediction",
)
REVIEW_REASONS = ("insufficient_data", "high_uncertainty", "no_prediction")
SIGNAL_DIRECTIONS = ("increases_risk", "decreases_risk")
BRIEF_STATUSES = ("awaiting_review", "accepted", "rejected")
USER_ROLES = ("advisor", "admin")
DECISION_ACTIONS = ("accept", "reject", "edit")
LLM_CALL_STATUSES = ("ok", "validation_failed", "api_error")

PREDICTION_BATCH_SCHEMA_VERSION = "prediction-batch.v1"


class Base(DeclarativeBase):
    """Declarative base for all LearnGuard entities."""


# --- §6.3 ml_predictions (pipeline-owned — READ ONLY) ---------------------------


class MLPrediction(Base):
    """Read-only mapping of ``ml_predictions`` (ml-data/docs/M5_PREDICTION_BATCH.md §4).

    PK (code_module, code_presentation, student_key) — always the latest run
    thanks to upsert semantics. NEVER write to this table from the backend.
    """

    __tablename__ = "ml_predictions"
    __table_args__ = (
        Index("ml_predictions_status_idx", "status"),
        Index("ml_predictions_model_version_idx", "model_version"),
    )

    code_module: Mapped[str] = mapped_column(String(8), primary_key=True)
    code_presentation: Mapped[str] = mapped_column(String(8), primary_key=True)
    student_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    split: Mapped[str] = mapped_column(String(12), nullable=False)
    risk_score: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    predicted_label: Mapped[int | None] = mapped_column(SMALLINT)
    threshold: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=False)
    uncertainty: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    review_reason: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(128), nullable=False)
    cutoff_day: Mapped[int] = mapped_column(INTEGER, nullable=False)
    top_signals_json: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )


# --- §6.1 users ------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("role IN ('advisor', 'admin')"),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# --- §6.4 briefs -------------------------------------------------------------------


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        Index(
            "uq_briefs_prediction_version",
            "code_module",
            "code_presentation",
            "student_key",
            "version",
            unique=True,
        ),
    )

    brief_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    student_key: Mapped[str] = mapped_column(CHAR(16), nullable=False)
    code_module: Mapped[str] = mapped_column(String(8), nullable=False)
    code_presentation: Mapped[str] = mapped_column(String(8), nullable=False)
    version: Mapped[int] = mapped_column(INTEGER, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(24),
        CheckConstraint("status IN ('awaiting_review', 'accepted', 'rejected')"),
        nullable=False,
        server_default=text("'awaiting_review'"),
    )
    created_by: Mapped[int | None] = mapped_column(
        BIGINT, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# --- §6.5 decisions -----------------------------------------------------------------


class Decision(Base):
    __tablename__ = "decisions"

    decision_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    brief_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("briefs.brief_id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.user_id"), nullable=False
    )
    action: Mapped[str] = mapped_column(
        String(8),
        CheckConstraint("action IN ('accept', 'reject', 'edit')"),
        nullable=False,
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# --- §6.6 llm_calls -------------------------------------------------------------------


class LLMCall(Base):
    __tablename__ = "llm_calls"

    call_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    brief_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("briefs.brief_id"))
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(
        INTEGER, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        INTEGER, nullable=False, server_default=text("0")
    )
    cost_usd: Mapped[float | None] = mapped_column(NUMERIC(12, 6))
    latency_ms: Mapped[int] = mapped_column(INTEGER, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        CheckConstraint("status IN ('ok', 'validation_failed', 'api_error')"),
        nullable=False,
    )
    called_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# --- §6.7 audit_logs --------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_entity", "entity", "entity_id"),
        Index("idx_audit_user", "user_id", "at"),
    )

    log_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.user_id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


BACKEND_OWNED_ENTITIES = (User, Brief, Decision, LLMCall, AuditLog)

__all__ = [
    "AuditLog",
    "BACKEND_OWNED_ENTITIES",
    "Base",
    "Brief",
    "BRIEF_STATUSES",
    "DECISION_ACTIONS",
    "Decision",
    "LLMCall",
    "LLM_CALL_STATUSES",
    "MLPrediction",
    "PREDICTION_BATCH_SCHEMA_VERSION",
    "PREDICTION_STATUSES",
    "REVIEW_REASONS",
    "SIGNAL_DIRECTIONS",
    "User",
    "USER_ROLES",
]
