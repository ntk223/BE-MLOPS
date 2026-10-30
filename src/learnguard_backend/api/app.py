"""M6 API application: FastAPI app factory, health route, advisor endpoints.

Reads the pipeline-owned ``ml_predictions`` table (read-only —
DB_CONTRACT.md v1.0 rev2). Every prediction access is written to
``audit_logs`` (no PII, no secrets).
"""

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from learnguard_backend import __version__
from learnguard_backend.api.schemas import DecisionRequest, StudentListFilters
from learnguard_backend.db.models import (
    AuditLogRecord,
    BriefRecord,
    MLPredictionRecord,
    UserRecord,
)
from learnguard_backend.db.repository import (
    PredictionPage,
    SQLAuditLogRepository,
    SQLMLPredictionRepository,
)
from learnguard_backend.db.session import get_session_factory
from learnguard_backend.db.entities import PREDICTION_STATUSES
from learnguard_backend.settings import BackendSettings

API_TITLE = "LearnGuard Backend"
API_DESCRIPTION = (
    "Advisor API for early-warning briefs: serves predictions produced by the "
    "M1-M5 data pipeline (read-only) and manages the advisor review workflow. "
    "No student-facing endpoints and no ML/data code imports by design."
)

CONFIGURED = "configured"
NOT_CONFIGURED = "not_configured"
REACHABLE = "reachable"
UNREACHABLE = "unreachable"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


# --- Response models (None-safe: predicted_label may be NULL) -----------------


class SignalOut(BaseModel):
    feature_name: str
    contribution: float
    direction: str


class PredictionOut(BaseModel):
    code_module: str
    code_presentation: str
    student_key: str
    split: str
    risk_score: float | None = None
    predicted_label: int | None = None
    threshold: float | None = None
    uncertainty: float | None = None
    status: str
    review_reason: str | None = None
    model_version: str
    snapshot_version: str
    cutoff_day: int
    top_signals: list[SignalOut] = Field(default_factory=list)
    run_id: str
    scored_at: Any = None


class PredictionPageOut(BaseModel):
    items: list[PredictionOut]
    total: int
    limit: int
    offset: int


class DependencyHealth(BaseModel):
    database: str
    llm: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    dependencies: DependencyHealth


def _parse_top_signals(raw: str | None) -> list[SignalOut]:
    """Parse ``top_signals_json`` defensively — never crash serialization."""
    if not raw:
        return []
    try:
        signals = json.loads(raw)
    except json.JSONDecodeError:
        return []
    parsed: list[SignalOut] = []
    for signal in signals if isinstance(signals, list) else []:
        if not isinstance(signal, dict):
            continue
        try:
            parsed.append(
                SignalOut(
                    feature_name=str(signal["feature_name"]),
                    contribution=float(signal["contribution"]),
                    direction=str(signal["direction"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


def _to_out(record: MLPredictionRecord) -> PredictionOut:
    return PredictionOut(
        code_module=record.code_module,
        code_presentation=record.code_presentation,
        student_key=record.student_key,
        split=record.split,
        risk_score=record.risk_score,
        predicted_label=record.predicted_label,
        threshold=record.threshold,
        uncertainty=record.uncertainty,
        status=record.status,
        review_reason=record.review_reason,
        model_version=record.model_version,
        snapshot_version=record.snapshot_version,
        cutoff_day=record.cutoff_day,
        top_signals=_parse_top_signals(record.top_signals_json),
        run_id=record.run_id,
        scored_at=record.scored_at.isoformat() if record.scored_at else None,
    )


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Settings are loaded once at startup; an invalid configuration fails fast
    here instead of at request time. Pass an explicit ``settings`` in tests.
    """
    if settings is None:
        settings = BackendSettings.from_environment()

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=__version__,
    )
    app.state.settings = settings

    _session_factory: sessionmaker[Session] | None = None
    if settings.database_url:
        _session_factory = get_session_factory(settings)

    def _repositories() -> tuple[
        SQLMLPredictionRepository, SQLAuditLogRepository
    ]:
        if _session_factory is None:
            raise HTTPException(
                status_code=503,
                detail="database_not_configured (set DATABASE_URL)",
            )
        return (
            SQLMLPredictionRepository(_session_factory),
            SQLAuditLogRepository(_session_factory),
        )

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["ops"],
        summary="Liveness and dependency configuration status",
    )
    def health() -> HealthResponse:
        database = REACHABLE if _session_factory is not None else NOT_CONFIGURED
        if _session_factory is not None:
            try:
                with _session_factory() as session:
                    session.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 — health must never raise
                database = UNREACHABLE
        return HealthResponse(
            status="ok",
            app=API_TITLE,
            version=__version__,
            dependencies=DependencyHealth(
                database=database,
                llm=(
                    CONFIGURED
                    if settings.llm_api_key or settings.llm_fallback_model
                    else NOT_CONFIGURED
                ),
            ),
        )

    @app.get(
        "/predictions",
        response_model=PredictionPageOut,
        tags=["advisor"],
        summary="Paginated prediction list for the advisor review queue",
    )
    def list_predictions(
        status: Annotated[str | None, Query(description="4-value status filter")] = None,
        code_module: Annotated[str | None, Query(max_length=8)] = None,
        code_presentation: Annotated[str | None, Query(max_length=8)] = None,
        order_by: Annotated[
            str, Query(description="risk_score | uncertainty | scored_at")
        ] = "risk_score",
        order: Annotated[
            str | None, Query(description="Alias of order_by (accepted for convenience)")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PredictionPageOut:
        prediction_repo, audit_repo = _repositories()
        try:
            page: PredictionPage = prediction_repo.list(
                status=status,
                code_module=code_module,
                code_presentation=code_presentation,
                order_by=order or order_by,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_repo.append(
            _audit_event(
                action="prediction_list",
                entity_id=None,
                payload={
                    "status": status,
                    "code_module": code_module,
                    "code_presentation": code_presentation,
                    "order_by": order_by,
                    "limit": limit,
                    "offset": offset,
                    "returned": len(page.items),
                },
            )
        )
        return PredictionPageOut(
            items=[_to_out(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    @app.get(
        "/predictions/{code_module}/{code_presentation}/{student_key}",
        response_model=PredictionOut,
        tags=["advisor"],
        summary="Detail for one prediction (composite PK of ml_predictions)",
    )
    def get_prediction(
        code_module: str,
        code_presentation: str,
        student_key: str,
    ) -> PredictionOut:
        prediction_repo, audit_repo = _repositories()
        record = prediction_repo.get(code_module, code_presentation, student_key)
        if record is None:
            raise HTTPException(status_code=404, detail="prediction_not_found")
        audit_repo.append(
            _audit_event(
                action="prediction_detail",
                entity_id=record.student_key,
                payload={
                    "code_module": record.code_module,
                    "code_presentation": record.code_presentation,
                    "status": record.status,
                    "run_id": record.run_id,
                },
            )
        )
        return _to_out(record)

    @app.get(
        "/predictions/by-student/{student_key}",
        response_model=PredictionPageOut,
        tags=["advisor"],
        summary="All predictions for one pseudonymous student (may span modules)",
    )
    def list_predictions_for_student(
        student_key: str,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> PredictionPageOut:
        prediction_repo, audit_repo = _repositories()
        try:
            page = prediction_repo.list(
                student_key=student_key, order_by="scored_at",
                limit=limit, offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_repo.append(
            _audit_event(
                action="prediction_student_lookup",
                entity_id=student_key,
                payload={"returned": len(page.items)},
            )
        )
        return PredictionPageOut(
            items=[_to_out(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )

    return app


def _audit_event(
    action: str, entity_id: str | None, payload: dict[str, Any]
) -> AuditLogRecord:
    return AuditLogRecord(
        log_id=None,
        user_id=None,  # TODO(M6 auth): fill from the authenticated principal
        action=action,
        entity="ml_prediction",
        entity_id=entity_id,
        payload=payload,
    )


class AuthenticationController:
    def login(self, email: str, password: str) -> dict[str, str]:
        """TODO: authenticate, issue token, and audit the login attempt."""
        raise NotImplementedError

    def current_user(self, token: str) -> UserRecord:
        """TODO: resolve the current user and enforce active-account policy."""
        raise NotImplementedError


class PredictionController:
    def list_students(self, filters: StudentListFilters) -> list[MLPredictionRecord]:
        """TODO: return paginated predictions for an advisor's review queue."""
        raise NotImplementedError

    def get_student(
        self, code_module: str, code_presentation: str, student_key: str
    ) -> MLPredictionRecord:
        """TODO: return a prediction detail and safe display metadata."""
        raise NotImplementedError


class BriefController:
    def generate(
        self, code_module: str, code_presentation: str, student_key: str,
        actor: UserRecord,
    ) -> BriefRecord:
        """TODO: request an M8 draft and persist a new brief version."""
        raise NotImplementedError

    def edit(self, brief_id: int, content: dict[str, Any],
             actor: UserRecord) -> BriefRecord:
        """TODO: save edits as a new immutable brief version."""
        raise NotImplementedError

    def decide(self, brief_id: int, request: DecisionRequest,
               actor: UserRecord) -> BriefRecord:
        """TODO: persist advisor accept/reject and feedback."""
        raise NotImplementedError


class LearnGuardApi:
    """Owns API route wiring and application lifecycle."""

    def create_app(self) -> FastAPI:
        """Build the FastAPI app via the module-level factory."""
        return create_app()
