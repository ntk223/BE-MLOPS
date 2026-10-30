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
    Decision,
    MLPrediction,
)
from learnguard_backend.db.models import (
    AuditLogRecord,
    DecisionRecord,
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
                return DecisionRecord(
                    decision_id=row.decision_id,
                    brief_id=row.brief_id,
                    user_id=row.user_id,
                    action=row.action,
                    feedback=row.feedback,
                )
