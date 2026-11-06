"""M6 API application: FastAPI app factory, health route, advisor endpoints.

Reads the pipeline-owned ``ml_predictions`` table (read-only —
DB_CONTRACT.md v1.0 rev2). Every prediction access is written to
``audit_logs`` (no PII, no secrets).
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from learnguard_backend import __version__
from learnguard_backend.api.schemas import DecisionRequest, StudentListFilters
from learnguard_backend.db.models import (
    AuditLogRecord,
    BriefRecord,
    DecisionRecord,
    LLMCallRecord,
    MLPredictionRecord,
    UserRecord,
)
from learnguard_backend.db.repository import (
    PredictionPage,
    SQLAuditLogRepository,
    SQLBriefRepository,
    SQLDecisionRepository,
    SQLLLMCallRepository,
    SQLMLPredictionRepository,
    SQLUserRepository,
)
from learnguard_backend.db.session import get_session_factory
from learnguard_backend.db.entities import PREDICTION_STATUSES
from learnguard_backend.services.briefs import (
    STATUS_UNAVAILABLE,
    AdvisorBriefService,
    build_context,
)
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

EVALUATIONS_DEFAULT_LIMIT = 20
EVALUATIONS_MAX_LIMIT = 100
EVAL_REPORT_SCHEMA = "eval-report.v1"
# run_id is a single directory name: no separators, no leading dot — this
# blocks path traversal out of EVALUATIONS_DIR at the validation layer.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
    # Thống kê hành vi/học thuật GỐC của SV tại cutoff (features_json trong DB).
    features: dict[str, float | None] = Field(default_factory=dict)
    run_id: str
    scored_at: Any = None


class PredictionPageOut(BaseModel):
    items: list[PredictionOut]
    total: int
    limit: int
    offset: int


class BriefGenerateRequest(BaseModel):
    code_module: str = Field(min_length=1, max_length=8)
    code_presentation: str = Field(min_length=1, max_length=8)
    student_key: str = Field(min_length=1, max_length=16)


class BriefReasonOut(BaseModel):
    text: str
    evidence_features: list[str] = Field(default_factory=list)


class BriefOut(BaseModel):
    brief_id: int
    code_module: str
    code_presentation: str
    student_key: str
    version: int
    status: str  # generated | fallback_review | unavailable
    summary: str
    reasons: list[BriefReasonOut] = Field(default_factory=list)
    support_suggestions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    model_name: str | None = None
    latency_ms: int | None = None
    created_at: str | None = None
    edited_at: str | None = None  # set after an advisor edit (content.edited_at)


class BriefDecisionRequestBody(BaseModel):
    decision: str  # accepted | rejected (validated in-route for a 400)


class BriefDecisionOut(BaseModel):
    brief_id: int
    decision: str
    decided_at: str


class DependencyHealth(BaseModel):
    database: str
    llm: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    dependencies: DependencyHealth


class EvaluationRunSummaryOut(BaseModel):
    run_id: str
    created_at: str | None = None
    subjects: list[str] = Field(default_factory=list)
    combined_summary: dict[str, int] = Field(default_factory=dict)
    overall_status: str  # pass | warn | fail
    reports_available: list[str] = Field(default_factory=list)


class EvaluationListOut(BaseModel):
    runs: list[EvaluationRunSummaryOut]
    total: int
    limit: int


class EvaluationDetailOut(BaseModel):
    run_id: str
    created_at: str | None = None
    reports: dict[str, dict[str, Any]] = Field(default_factory=dict)


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


def _parse_features(raw: str | None) -> dict[str, float | None]:
    """Parse ``features_json`` defensively — never crash serialization."""
    if not raw:
        return {}
    try:
        features = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(features, dict):
        return {}
    parsed: dict[str, float | None] = {}
    for name, value in features.items():
        if not isinstance(name, str):
            continue
        if value is None:
            parsed[name] = None
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed[name] = float(value)
    return parsed


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    """Read a JSON file defensively — ``None`` for missing/corrupt/non-dict."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _is_valid_run_id(run_id: str) -> bool:
    """Only ``[A-Za-z0-9._-]``, no leading dot — blocks path traversal."""
    return bool(run_id) and not run_id.startswith(".") and bool(
        _RUN_ID_RE.fullmatch(run_id)
    )


def _load_manifest(run_dir: Path) -> dict[str, Any] | None:
    """Return a valid eval-report manifest or ``None`` (run is skipped)."""
    manifest = _read_json_dict(run_dir / "manifest.json")
    if manifest is None or manifest.get("schema_version") != EVAL_REPORT_SCHEMA:
        return None
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        return None
    return manifest


def _load_scorecard(path: Path) -> dict[str, Any] | None:
    """Return a scorecard dict (with a string ``subject``) or ``None``."""
    card = _read_json_dict(path)
    if card is None or not isinstance(card.get("subject"), str):
        return None
    return card


def _combined_summary(scorecards: list[dict[str, Any]]) -> dict[str, int]:
    """Sum the per-scorecard summaries into one combined tally."""
    keys = ("total", "pass", "warn", "fail", "skipped")
    combined = dict.fromkeys(keys, 0)
    for card in scorecards:
        summary = card.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        for key in keys:
            value = summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                combined[key] += value
    return combined


def _overall_status(summary: dict[str, int]) -> str:
    """fail if any check failed, warn if any warned, otherwise pass."""
    if summary.get("fail", 0) > 0:
        return "fail"
    if summary.get("warn", 0) > 0:
        return "warn"
    return "pass"


def _scan_runs(root: Path) -> list[dict[str, Any]]:
    """Scan ``root`` for valid evaluation runs (corrupt subdirs are skipped).

    Never raises for a missing/unreadable root: the caller decides whether
    that is a 404/empty-list situation, not a crash.
    """
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    runs: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        manifest = _load_manifest(entry)
        if manifest is None:
            continue
        scorecards: list[dict[str, Any]] = []
        available: list[str] = []
        try:
            report_paths = sorted(entry.glob("eval_*.json"))
        except OSError:
            report_paths = []
        for path in report_paths:
            card = _load_scorecard(path)
            if card is not None:
                scorecards.append(card)
                available.append(path.name)
        subjects = manifest.get("subjects")
        if not isinstance(subjects, list):
            subjects = [
                card["subject"] for card in scorecards
            ]
        subjects = [s for s in subjects if isinstance(s, str)]
        summary = _combined_summary(scorecards)
        created_at = manifest.get("created_at")
        runs.append(
            {
                "run_id": manifest["run_id"],
                "created_at": (
                    created_at if isinstance(created_at, str) else None
                ),
                "subjects": subjects,
                "combined_summary": summary,
                "overall_status": _overall_status(summary),
                "reports_available": available,
            }
        )
    return runs


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
        features=_parse_features(record.features_json),
        run_id=record.run_id,
        scored_at=record.scored_at.isoformat() if record.scored_at else None,
    )


def create_app(
    settings: BackendSettings | None = None, llm_service: Any = None
) -> FastAPI:
    """Build the FastAPI application.

    Settings are loaded once at startup; an invalid configuration fails fast
    here instead of at request time. Pass an explicit ``settings`` in tests.
    ``llm_service`` injects a (fake) LLM for tests; when omitted the shared
    ``get_llm_service()`` is resolved lazily on first brief generation.
    """
    if settings is None:
        settings = BackendSettings.from_environment()

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=__version__,
    )
    app.state.settings = settings

    # The advisor SPA (frontend repo, default Vite origin) calls this API
    # cross-origin. Origins are configurable; "*" is deliberately not used.
    allowed_origins = [
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

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

    def _brief_repositories() -> tuple[
        SQLBriefRepository,
        SQLDecisionRepository,
        SQLLLMCallRepository,
        SQLUserRepository,
    ]:
        if _session_factory is None:
            raise HTTPException(
                status_code=503,
                detail="database_not_configured (set DATABASE_URL)",
            )
        return (
            SQLBriefRepository(_session_factory),
            SQLDecisionRepository(_session_factory),
            SQLLLMCallRepository(_session_factory),
            SQLUserRepository(_session_factory),
        )

    # M8: the brief service uses the shared LLM unless a test fake is
    # injected. Without a configured LLM, generation degrades to
    # ``unavailable`` instead of erroring.
    brief_service = AdvisorBriefService(llm=llm_service)
    llm_configured = (
        llm_service is not None
        or bool(settings.llm_api_key or settings.llm_fallback_model)
    )

    def _audit_evaluation(
        action: str, entity_id: str | None, payload: dict[str, Any]
    ) -> None:
        """Best-effort audit for the read-only evaluation endpoints.

        EVALUATIONS_DIR is filesystem-only, so /evaluations works without a
        database; when no DB is configured the audit entry is skipped
        instead of failing the request.
        """
        if _session_factory is None:
            return
        SQLAuditLogRepository(_session_factory).append(
            _audit_event(
                action=action,
                entity_id=entity_id,
                entity="evaluation",
                payload=payload,
            )
        )

    def _evaluations_root() -> Path:
        if not settings.evaluations_dir:
            raise HTTPException(
                status_code=503,
                detail="evaluations_dir_not_configured (set EVALUATIONS_DIR)",
            )
        return Path(settings.evaluations_dir)

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
        "/evaluations",
        response_model=EvaluationListOut,
        tags=["evaluations"],
        summary="List published evaluation runs (read-only scorecards)",
    )
    def list_evaluations(
        limit: Annotated[
            int, Query(ge=1, le=EVALUATIONS_MAX_LIMIT)
        ] = EVALUATIONS_DEFAULT_LIMIT,
    ) -> EvaluationListOut:
        root = _evaluations_root()
        runs = _scan_runs(root)
        runs.sort(
            key=lambda run: run["created_at"] or "", reverse=True
        )
        _audit_evaluation(
            action="evaluation_list",
            entity_id=None,
            payload={
                "limit": limit,
                "total": len(runs),
                "returned": min(len(runs), limit),
            },
        )
        return EvaluationListOut(
            runs=[EvaluationRunSummaryOut(**run) for run in runs[:limit]],
            total=len(runs),
            limit=limit,
        )

    @app.get(
        "/evaluations/{run_id}",
        response_model=EvaluationDetailOut,
        tags=["evaluations"],
        summary="Full scorecards for one evaluation run, keyed by subject",
    )
    def get_evaluation(run_id: str) -> EvaluationDetailOut:
        if not _is_valid_run_id(run_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "invalid_run_id (only [A-Za-z0-9._-], no leading dot)"
                ),
            )
        run_dir = _evaluations_root() / run_id
        manifest = _load_manifest(run_dir)
        if manifest is None:
            raise HTTPException(status_code=404, detail="evaluation_not_found")
        reports: dict[str, dict[str, Any]] = {}
        for path in sorted(run_dir.glob("eval_*.json")):
            card = _load_scorecard(path)
            if card is not None:
                subject = card["subject"]
                reports[subject] = card
        _audit_evaluation(
            action="evaluation_read",
            entity_id=run_id,
            payload={"subjects": sorted(reports)},
        )
        created_at = manifest.get("created_at")
        return EvaluationDetailOut(
            run_id=str(manifest["run_id"]),
            created_at=created_at if isinstance(created_at, str) else None,
            reports=reports,
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

    @app.post(
        "/briefs/generate",
        response_model=BriefOut,
        tags=["advisor"],
        summary="Generate (or regenerate) an advisor brief for one prediction",
    )
    def generate_brief(body: BriefGenerateRequest) -> BriefOut:
        prediction_repo, audit_repo = _repositories()
        brief_repo, _, llm_call_repo, _ = _brief_repositories()
        record = prediction_repo.get(
            body.code_module, body.code_presentation, body.student_key
        )
        if record is None:
            raise HTTPException(status_code=404, detail="prediction_not_found")

        prediction_ref = (
            f"{record.code_module}/{record.code_presentation}/{record.student_key}"
        )
        if llm_configured:
            # Minimal PII-free context only (hashed student_key, aggregates).
            result = brief_service.generate(prediction_ref, build_context(record))
        else:
            result = brief_service.make_fallback(
                prediction_ref, "llm_not_configured", status=STATUS_UNAVAILABLE
            )

        content = _brief_content(result)
        citations = _cited_evidence(result)
        active = brief_repo.get_active(
            record.code_module, record.code_presentation, record.student_key
        )
        if active is not None:
            # Regenerate: version+1 on the same brief row (mock behaviour).
            row = brief_repo.bump_version(
                active.brief_id, active.version + 1, content, citations
            )
        else:
            version = brief_repo.next_version(
                record.code_module,
                record.code_presentation,
                record.student_key,
            )
            row = brief_repo.insert(
                BriefRecord(
                    brief_id=None,
                    code_module=record.code_module,
                    code_presentation=record.code_presentation,
                    student_key=record.student_key,
                    version=version,
                    content=content,
                    citations=citations,
                    status="awaiting_review",
                    created_by=None,
                )
            )

        if llm_configured:
            llm_call_repo.save(
                LLMCallRecord(
                    call_id=None,
                    brief_id=row.brief_id,
                    model=result.model_name
                    or settings.llm_model
                    or "unknown",
                    latency_ms=result.latency_ms or 0,
                    status=_llm_call_status(result),
                    cost_usd=result.cost_usd,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
            )
        audit_repo.append(
            _audit_event(
                action="brief_generate",
                entity_id=str(row.brief_id),
                entity="brief",
                payload={
                    "code_module": record.code_module,
                    "code_presentation": record.code_presentation,
                    "student_key": record.student_key,
                    "version": row.version,
                    "generation_status": result.status,
                    "error_reason": result.error_reason,
                },
            )
        )
        return _to_brief_out(row)

    @app.get(
        "/briefs/{brief_id}",
        response_model=BriefOut,
        tags=["advisor"],
        summary="One advisor brief by id",
    )
    def get_brief(brief_id: int) -> BriefOut:
        brief_repo, _, _, _ = _brief_repositories()
        row = brief_repo.get(brief_id)
        if row is None:
            raise HTTPException(status_code=404, detail="brief_not_found")
        return _to_brief_out(row)

    @app.put(
        "/briefs/{brief_id}",
        response_model=BriefOut,
        tags=["advisor"],
        summary=(
            "Advisor edit of a brief's editable content (no version bump, "
            "evidence citations untouched)"
        ),
    )
    def edit_brief(brief_id: int, body: dict[str, Any]) -> BriefOut:
        """Save advisor edits in place.

        Version is NOT bumped (only regeneration creates a new version),
        ``status`` and the persisted ``citations`` column are untouched.
        The edit is recorded in ``content`` (edited_at/edited_by/source_history)
        and in ``audit_logs`` (action ``brief_edit``).
        """
        brief_repo, _, _, user_repo = _brief_repositories()
        _, audit_repo = _repositories()
        row = brief_repo.get(brief_id)
        if row is None:
            raise HTTPException(status_code=404, detail="brief_not_found")

        updates = _validated_brief_edits(body)
        if not updates:
            raise HTTPException(
                status_code=400,
                detail=(
                    "empty_edit_payload (supply at least one of: summary, "
                    "reasons, support_suggestions, limitations)"
                ),
            )

        # TODO(M6 auth): replace the system principal with the authenticated
        # advisor; audit_logs.user_id is NOT NULL so an actor is required.
        user_id = user_repo.get_or_create_system_user()
        content = dict(row.content or {})
        content.update(updates)
        edited_at = datetime.now(timezone.utc).isoformat()
        content["edited_at"] = edited_at
        content["edited_by"] = SQLUserRepository.SYSTEM_EMAIL
        history = content.get("source_history")
        history = (
            [item for item in history if isinstance(item, str)]
            if isinstance(history, list)
            else []
        )
        if "advisor_edit" not in history:
            history.append("advisor_edit")
        content["source_history"] = history

        updated = brief_repo.update_content(brief_id, content)
        audit_repo.append(
            _audit_event(
                action="brief_edit",
                entity_id=str(brief_id),
                entity="brief",
                user_id=user_id,
                payload={
                    "fields": sorted(updates),
                    "edited_at": edited_at,
                },
            )
        )
        return _to_brief_out(updated)

    @app.post(
        "/briefs/{brief_id}/decision",
        response_model=BriefDecisionOut,
        tags=["advisor"],
        summary="Record the advisor's accept/reject decision for a brief",
    )
    def decide_brief(brief_id: int, body: BriefDecisionRequestBody) -> BriefDecisionOut:
        if body.decision not in ("accepted", "rejected"):
            raise HTTPException(
                status_code=400,
                detail="invalid_decision (must be 'accepted' or 'rejected')",
            )
        brief_repo, decision_repo, _, user_repo = _brief_repositories()
        _, audit_repo = _repositories()
        row = brief_repo.get(brief_id)
        if row is None:
            raise HTTPException(status_code=404, detail="brief_not_found")

        # TODO(M6 auth): replace the system principal with the authenticated
        # advisor; decisions.user_id is NOT NULL so an actor is required.
        user_id = user_repo.get_or_create_system_user()
        action = "accept" if body.decision == "accepted" else "reject"
        decision = decision_repo.save(
            DecisionRecord(
                decision_id=None,
                brief_id=brief_id,
                user_id=user_id,
                action=action,
                feedback=None,
            )
        )
        brief_repo.set_status(brief_id, "accepted" if action == "accept" else "rejected")
        audit_repo.append(
            _audit_event(
                action="brief_decision",
                entity_id=str(brief_id),
                entity="brief",
                payload={"decision": body.decision, "action": action},
            )
        )
        decided_at = decision.decided_at or datetime.now(timezone.utc)
        return BriefDecisionOut(
            brief_id=brief_id,
            decision=body.decision,
            decided_at=decided_at.isoformat(),
        )

    return app


def _audit_event(
    action: str,
    entity_id: str | None,
    payload: dict[str, Any],
    entity: str = "ml_prediction",
    user_id: int | None = None,
) -> AuditLogRecord:
    return AuditLogRecord(
        log_id=None,
        user_id=user_id,  # TODO(M6 auth): fill from the authenticated principal
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=payload,
    )


def _brief_content(result: Any) -> dict[str, Any]:
    """Serialize a BriefGenerationResult into the briefs.content JSONB."""
    draft = result.draft
    return {
        "summary": draft.summary if draft else "",
        "reasons": [
            {
                "text": reason.text,
                "evidence_features": list(reason.evidence_features),
            }
            for reason in (draft.reasons if draft else [])
        ],
        "support_suggestions": (
            list(draft.support_template_ids) if draft else []
        ),
        "limitations": list(draft.limitations) if draft else [],
        "generation_status": result.status,
        "model_name": result.model_name,
        "latency_ms": result.latency_ms,
        "error_reason": result.error_reason,
        # Fresh LLM content replaces any previous advisor edit: the new
        # version has not been edited yet and starts with a clean history.
        "edited_at": None,
        "source_history": [],
    }


def _cited_evidence(result: Any) -> list[dict[str, Any]]:
    """Only evidence actually cited by a (grounded) reason is persisted."""
    if result.draft is None:
        return []
    cited = {
        name
        for reason in result.draft.reasons
        for name in reason.evidence_features
    }
    return [
        {
            "feature_name": evidence.feature_name,
            "observed_value": evidence.observed_value,
            "contribution": evidence.contribution,
        }
        for evidence in result.draft.evidence
        if evidence.feature_name in cited
    ]


def _llm_call_status(result: Any) -> str:
    """Map a generation result to the llm_calls.status enum."""
    if result.status == "generated":
        return "ok"
    if result.error_reason and result.error_reason.startswith("llm_error"):
        return "api_error"
    return "validation_failed"


def _to_brief_out(record: BriefRecord) -> BriefOut:
    content = record.content or {}
    reasons: list[BriefReasonOut] = []
    for item in content.get("reasons") or []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        features = item.get("evidence_features")
        reasons.append(
            BriefReasonOut(
                text=item["text"],
                evidence_features=[
                    name
                    for name in (features if isinstance(features, list) else [])
                    if isinstance(name, str)
                ],
            )
        )
    created_at = record.created_at
    raw_edited_at = content.get("edited_at")
    return BriefOut(
        brief_id=record.brief_id,
        code_module=record.code_module,
        code_presentation=record.code_presentation,
        student_key=record.student_key,
        version=record.version,
        status=str(content.get("generation_status") or STATUS_UNAVAILABLE),
        summary=str(content.get("summary") or ""),
        reasons=reasons,
        support_suggestions=[
            item
            for item in (content.get("support_suggestions") or [])
            if isinstance(item, str)
        ],
        limitations=[
            item
            for item in (content.get("limitations") or [])
            if isinstance(item, str)
        ],
        model_name=(
            content.get("model_name")
            if isinstance(content.get("model_name"), str)
            else None
        ),
        latency_ms=(
            content.get("latency_ms")
            if isinstance(content.get("latency_ms"), int)
            else None
        ),
        created_at=created_at.isoformat() if created_at else None,
        edited_at=raw_edited_at if isinstance(raw_edited_at, str) else None,
    )


def _validated_brief_edits(body: dict[str, Any]) -> dict[str, Any]:
    """Keep only the four editable brief fields and validate their shapes.

    Unknown keys (``brief_id``/``student_key``/``code_module``/...) are
    deliberately ignored — identity fields can never be edited. Raises 400
    when a present editable field has the wrong shape; returns ``{}`` when
    no editable field was supplied at all.
    """
    errors: list[str] = []
    updates: dict[str, Any] = {}

    if "summary" in body:
        summary = body["summary"]
        if not isinstance(summary, str) or not summary.strip():
            errors.append("summary must be a non-empty string")
        else:
            updates["summary"] = summary

    if "reasons" in body:
        raw_reasons = body["reasons"]
        if not isinstance(raw_reasons, list):
            errors.append("reasons must be a list of {text, evidence_features}")
        elif any(
            not isinstance(item, dict) or not isinstance(item.get("text"), str)
            for item in raw_reasons
        ):
            errors.append(
                "reasons must be a list of {text, evidence_features}"
            )
        else:
            updates["reasons"] = [
                {
                    "text": item["text"],
                    "evidence_features": [
                        name
                        for name in (
                            item.get("evidence_features")
                            if isinstance(item.get("evidence_features"), list)
                            else []
                        )
                        if isinstance(name, str)
                    ],
                }
                for item in raw_reasons
            ]

    for key in ("support_suggestions", "limitations"):
        if key in body:
            raw_items = body[key]
            if not isinstance(raw_items, list):
                errors.append(f"{key} must be a list of strings")
            else:
                updates[key] = [
                    item for item in raw_items if isinstance(item, str)
                ]

    if errors:
        raise HTTPException(
            status_code=400,
            detail="invalid_edit_payload: " + "; ".join(errors),
        )
    return updates


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
