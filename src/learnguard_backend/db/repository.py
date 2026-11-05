"""M7 repository: read-only access to ``ml_predictions`` + append services.

The ML prediction repository is STRICTLY read-only (DB_CONTRACT.md v1.0
rev2 §4/§10.4) — no save/update method exists by design. Backend-owned
tables expose append-only writers where the workflow requires them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from learnguard_backend.db.entities import (
    PREDICTION_STATUSES,
    AuditLog,
    Brief,
    Decision,
    LLMCall,
    MLPrediction,
    User,
)
from learnguard_backend.db.models import (
    AuditLogRecord,
    BriefRecord,
    DecisionRecord,
    LLMCallRecord,
    MLPredictionRecord,
)


@dataclass(frozen=True)
class PredictionPage:
    items: list[MLPredictionRecord]
    total: int
    limit: int
    offset: int


class MLPredictionRepository(Protocol):
    """Read-only contract over ``ml_predictions``."""

    def get(
        self, code_module: str, code_presentation: str, student_key: str
    ) -> MLPredictionRecord | None: ...

    def list(
        self,
        *,
        status: str | None = None,
        code_module: str | None = None,
        code_presentation: str | None = None,
        student_key: str | None = None,
        order_by: str = "risk_score",
        limit: int = 50,
        offset: int = 0,
    ) -> PredictionPage: ...


_ORDER_COLUMNS = {
    "risk_score": MLPrediction.risk_score,
    "uncertainty": MLPrediction.uncertainty,
    "scored_at": MLPrediction.scored_at,
}


class SQLMLPredictionRepository:
    """SQLAlchemy implementation of the read-only prediction repository."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _to_record(row: MLPrediction) -> MLPredictionRecord:
        return MLPredictionRecord(
            code_module=row.code_module,
            code_presentation=row.code_presentation,
            student_key=row.student_key,
            split=row.split,
            risk_score=row.risk_score,
            predicted_label=row.predicted_label,
            threshold=row.threshold,
            uncertainty=row.uncertainty,
            status=row.status,
            review_reason=row.review_reason,
            model_version=row.model_version,
            snapshot_version=row.snapshot_version,
            cutoff_day=row.cutoff_day,
            top_signals_json=row.top_signals_json,
            features_json=row.features_json,
            run_id=row.run_id,
            scored_at=row.scored_at,
        )

    def get(
        self, code_module: str, code_presentation: str, student_key: str
    ) -> MLPredictionRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(MLPrediction).where(
                    MLPrediction.code_module == code_module,
                    MLPrediction.code_presentation == code_presentation,
                    MLPrediction.student_key == student_key,
                )
            ).scalar_one_or_none()
            return self._to_record(row) if row is not None else None

    def list(
        self,
        *,
        status: str | None = None,
        code_module: str | None = None,
        code_presentation: str | None = None,
        student_key: str | None = None,
        order_by: str = "risk_score",
        limit: int = 50,
        offset: int = 0,
    ) -> PredictionPage:
        if status is not None and status not in PREDICTION_STATUSES:
            raise ValueError(
                f"status must be one of {PREDICTION_STATUSES}, got '{status}'"
            )
        if order_by not in _ORDER_COLUMNS:
            raise ValueError(f"order_by must be one of {sorted(_ORDER_COLUMNS)}")
        if limit < 1 or offset < 0:
            raise ValueError("limit must be >= 1 and offset must be >= 0")

        column = _ORDER_COLUMNS[order_by]
        statement = select(MLPrediction)
        if status is not None:
            statement = statement.where(MLPrediction.status == status)
        if code_module is not None:
            statement = statement.where(MLPrediction.code_module == code_module)
        if code_presentation is not None:
            statement = statement.where(
                MLPrediction.code_presentation == code_presentation
            )
        if student_key is not None:
            statement = statement.where(MLPrediction.student_key == student_key)
        count_statement = select(func.count()).select_from(statement.subquery())

        # NULLs last so incomplete rows never bury scored ones.
        statement = (
            statement.order_by(column.desc().nulls_last(), MLPrediction.student_key)
            .limit(limit)
            .offset(offset)
        )

        with self._session_factory() as session:
            total = session.execute(count_statement).scalar_one()
            rows: Sequence[MLPrediction] = session.execute(statement).scalars().all()
            return PredictionPage(
                items=[self._to_record(row) for row in rows],
                total=total,
                limit=limit,
                offset=offset,
            )


class DecisionRepository(Protocol):
    def save(self, decision: DecisionRecord) -> DecisionRecord: ...


class AuditLogRepository(Protocol):
    def append(self, event: AuditLogRecord) -> None: ...


class SQLAuditLogRepository:
    """Append-only audit writer (DB_CONTRACT.md §6.7)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, event: AuditLogRecord) -> None:
        with self._session_factory() as session:
            with session.begin():
                session.add(
                    AuditLog(
                        user_id=event.user_id,
                        action=event.action,
                        entity=event.entity,
                        entity_id=event.entity_id,
                        payload=event.payload,
                    )
                )


class SQLDecisionRepository:
    """Append-only decision writer (DB_CONTRACT.md §6.5)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, decision: DecisionRecord) -> DecisionRecord:
        with self._session_factory() as session:
            with session.begin():
                row = Decision(
                    brief_id=decision.brief_id,
                    user_id=decision.user_id,
                    action=decision.action,
                    feedback=decision.feedback,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                return DecisionRecord(
                    decision_id=row.decision_id,
                    brief_id=row.brief_id,
                    user_id=row.user_id,
                    action=row.action,
                    feedback=row.feedback,
                    decided_at=row.decided_at,
                )


class SQLBriefRepository:
    """Backend-owned ``briefs`` writer (DB_CONTRACT.md §6.4).

    Versioned drafts per prediction: regeneration either bumps the version of
    the active (non-rejected) brief in place or starts a new version row when
    the previous one was rejected by the advisor.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _to_record(row: Brief) -> BriefRecord:
        return BriefRecord(
            brief_id=row.brief_id,
            code_module=row.code_module,
            code_presentation=row.code_presentation,
            student_key=row.student_key,
            version=row.version,
            content=row.content or {},
            citations=list(row.citations or []),
            status=row.status,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    def get(self, brief_id: int) -> BriefRecord | None:
        with self._session_factory() as session:
            row = session.get(Brief, brief_id)
            return self._to_record(row) if row is not None else None

    def get_active(
        self, code_module: str, code_presentation: str, student_key: str
    ) -> BriefRecord | None:
        """Latest brief for the prediction whose status is not ``rejected``."""
        with self._session_factory() as session:
            row = session.execute(
                select(Brief)
                .where(
                    Brief.code_module == code_module,
                    Brief.code_presentation == code_presentation,
                    Brief.student_key == student_key,
                    Brief.status != "rejected",
                )
                .order_by(Brief.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            return self._to_record(row) if row is not None else None

    def next_version(
        self, code_module: str, code_presentation: str, student_key: str
    ) -> int:
        with self._session_factory() as session:
            current = session.execute(
                select(func.max(Brief.version)).where(
                    Brief.code_module == code_module,
                    Brief.code_presentation == code_presentation,
                    Brief.student_key == student_key,
                )
            ).scalar_one()
            return (current or 0) + 1

    def insert(self, record: BriefRecord) -> BriefRecord:
        with self._session_factory() as session:
            with session.begin():
                row = Brief(
                    student_key=record.student_key,
                    code_module=record.code_module,
                    code_presentation=record.code_presentation,
                    version=record.version,
                    content=record.content,
                    citations=record.citations,
                    status=record.status,
                    created_by=record.created_by,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                return self._to_record(row)

    def bump_version(
        self,
        brief_id: int,
        version: int,
        content: dict,
        citations: list[dict],
    ) -> BriefRecord:
        """Regenerate on the same brief row: version+1, content replaced and
        the brief returns to ``awaiting_review``."""
        with self._session_factory() as session:
            with session.begin():
                row = session.get(Brief, brief_id)
                row.version = version
                row.content = content
                row.citations = citations
                row.status = "awaiting_review"
                row.created_at = func.now()
                session.flush()
                session.refresh(row)
                return self._to_record(row)

    def set_status(self, brief_id: int, status: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                row = session.get(Brief, brief_id)
                row.status = status

    def update_content(self, brief_id: int, content: dict) -> BriefRecord:
        """Advisor edit: replace ``content`` in place (no version bump, no
        status change, citations untouched — M8 edit contract)."""
        with self._session_factory() as session:
            with session.begin():
                row = session.get(Brief, brief_id)
                row.content = content
                session.flush()
                session.refresh(row)
                return self._to_record(row)


class SQLLLMCallRepository:
    """Append-only ``llm_calls`` writer (DB_CONTRACT.md §6.6)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, record: LLMCallRecord) -> LLMCallRecord:
        with self._session_factory() as session:
            with session.begin():
                row = LLMCall(
                    brief_id=record.brief_id,
                    model=record.model,
                    prompt_tokens=record.prompt_tokens or 0,
                    completion_tokens=record.completion_tokens or 0,
                    cost_usd=record.cost_usd,
                    latency_ms=record.latency_ms or 0,
                    status=record.status,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                return LLMCallRecord(
                    call_id=row.call_id,
                    brief_id=row.brief_id,
                    model=row.model,
                    latency_ms=row.latency_ms,
                    status=row.status,
                    cost_usd=(
                        float(row.cost_usd) if row.cost_usd is not None else None
                    ),
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                )


class SQLUserRepository:
    """Minimal user lookup for unauthenticated decision writes.

    TODO(M6 auth): replace the shared system principal with the
    authenticated advisor from the request context.
    """

    SYSTEM_EMAIL = "system@learnguard.local"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_or_create_system_user(self) -> int:
        with self._session_factory() as session:
            with session.begin():
                existing = session.execute(
                    select(User).where(User.email == self.SYSTEM_EMAIL)
                ).scalar_one_or_none()
                if existing is not None:
                    return existing.user_id
                row = User(
                    email=self.SYSTEM_EMAIL,
                    full_name="LearnGuard System (unauthenticated)",
                    role="admin",
                    # Unusable placeholder hash — this account cannot log in.
                    password_hash="!no-login",
                    active=True,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                return row.user_id
