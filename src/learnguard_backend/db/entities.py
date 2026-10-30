"""M7 SQLAlchemy entities mapping DB_CONTRACT.md v1.0 §6.

Source of truth: docs/DB_CONTRACT.md (v1.0 — approved 2026-10-30).
The dataclass records in db/models.py stay as DTO contracts for services;
these ORM entities are the persistence mapping.
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- Enum domains (DB_CONTRACT.md §8) -----------------------------------------

PREDICTION_STATUSES = (
    "scored",
    "insufficient_data",
    "manual_review",
    "no_prediction",
)
BRIEF_STATUSES = ("awaiting_review", "accepted", "rejected")
USER_ROLES = ("advisor", "admin")
DECISION_ACTIONS = ("accept", "reject", "edit")
BATCH_STATUSES = ("importing", "completed", "failed")
LLM_CALL_STATUSES = ("ok", "validation_failed", "api_error")

PREDICTION_BATCH_SCHEMA_VERSION = "prediction-batch.v1"
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = (PREDICTION_BATCH_SCHEMA_VERSION,)


class Base(DeclarativeBase):
    """Declarative base for all LearnGuard entities."""


# --- §6.1 users ----------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
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
        nullable=False, server_default=text("now()")
    )


# --- §6.2 model_versions --------------------------------------------------------


class ModelVersion(Base):
    __tablename__ = "model_versions"

    model_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    feature_list_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    threshold: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


# --- §6.3 prediction_batches ----------------------------------------------------


class PredictionBatch(Base):
    __tablename__ = "prediction_batches"

    batch_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("model_versions.model_version"),
        nullable=False,
    )
    snapshot_version: Mapped[str] = mapped_column(String(128), nullable=False)
    cutoff_day: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    row_count: Mapped[int] = mapped_column(INTEGER, nullable=False)
    inserted_rows: Mapped[int] = mapped_column(
        INTEGER, nullable=False, server_default=text("0")
    )
    skipped_rows: Mapped[int] = mapped_column(
        INTEGER, nullable=False, server_default=text("0")
    )
    predictions_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("status IN ('importing', 'completed', 'failed')"),
        nullable=False,
    )
    imported_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


# --- §6.4 predictions -----------------------------------------------------------


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        # 1 sinh viên 1 lần mỗi batch.
        Index(
            "uq_predictions_batch_student",
            "batch_id",
            "student_key",
            unique=True,
        ),
        Index("idx_predictions_queue", "status", "risk_score"),
        Index("idx_predictions_module", "code_module", "code_presentation"),
    )

    prediction_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("prediction_batches.batch_id"), nullable=False
    )
    student_key: Mapped[str] = mapped_column(CHAR(16), nullable=False)
    code_module: Mapped[str] = mapped_column(String(4), nullable=False)
    code_presentation: Mapped[str] = mapped_column(String(5), nullable=False)
    split: Mapped[str] = mapped_column(String(12), nullable=False)
    risk_score: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    predicted_label: Mapped[int | None] = mapped_column(SMALLINT)
    threshold: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    uncertainty: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    status: Mapped[str] = mapped_column(
        String(24),
        CheckConstraint("status IN ('scored', 'insufficient_data', 'manual_review', "
              "'no_prediction')"),
        nullable=False,
    )
    review_reason: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(128), nullable=False)
    cutoff_day: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    top_signals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scored_at: Mapped[datetime] = mapped_column(nullable=False)


# --- §6.5 briefs -----------------------------------------------------------------


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        Index("uq_briefs_prediction_version", "prediction_id", "version", unique=True),
    )

    brief_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("predictions.prediction_id"), nullable=False
    )
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
        nullable=False, server_default=text("now()")
    )


# --- §6.6 decisions --------------------------------------------------------------


class Decision(Base):
    __tablename__ = "decisions"

    decision_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
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
        nullable=False, server_default=text("now()")
    )


# --- §6.7 llm_calls ---------------------------------------------------------------


class LLMCall(Base):
    __tablename__ = "llm_calls"

    call_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
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
        nullable=False, server_default=text("now()")
    )


# --- §6.8 audit_logs ---------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_entity", "entity", "entity_id"),
        Index("idx_audit_user", "user_id", "at"),
    )

    log_id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.user_id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BIGINT)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))


ALL_ENTITIES = (
    User,
    ModelVersion,
    PredictionBatch,
    Prediction,
    Brief,
    Decision,
    LLMCall,
    AuditLog,
)

__all__ = [
    "ALL_ENTITIES",
    "AuditLog",
    "Base",
    "Brief",
    "Decision",
    "LLMCall",
    "LLM_CALL_STATUSES",
    "ModelVersion",
    "PREDICTION_BATCH_SCHEMA_VERSION",
    "PREDICTION_STATUSES",
    "Prediction",
    "PredictionBatch",
    "SUPPORTED_SCHEMA_VERSIONS",
    "User",
    "USER_ROLES",
    "BRIEF_STATUSES",
    "BATCH_STATUSES",
    "DECISION_ACTIONS",
]
