"""M6 API application: FastAPI app factory, health route, and error contract.

The factory owns route wiring and application lifecycle. Controllers below are
still design contracts for later milestones (auth, predictions, briefs).
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from learnguard_backend import __version__
from learnguard_backend.api.schemas import DecisionRequest, StudentListFilters
from learnguard_backend.db.models import BriefRecord, PredictionRecord, UserRecord
from learnguard_backend.services.batch_import import BatchImportReport
from learnguard_backend.settings import BackendSettings

API_TITLE = "LearnGuard Backend"
API_DESCRIPTION = (
    "Advisor API for early-warning briefs: serves predictions produced by the "
    "M1-M5 data pipeline and manages the advisor review workflow. No student-"
    "facing endpoints and no direct ML/data code imports by design."
)

CONFIGURED = "configured"
NOT_CONFIGURED = "not_configured"


class DependencyHealth(BaseModel):
    database: str
    llm: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    dependencies: DependencyHealth


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

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["ops"],
        summary="Liveness and dependency configuration status",
    )
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=API_TITLE,
            version=__version__,
            dependencies=DependencyHealth(
                database=(
                    CONFIGURED if settings.database_url else NOT_CONFIGURED
                ),
                llm=(
                    CONFIGURED
                    if settings.llm_api_key or settings.llm_fallback_model
                    else NOT_CONFIGURED
                ),
            ),
        )

    return app


class AuthenticationController:
    def login(self, email: str, password: str) -> dict[str, str]:
        """TODO: authenticate, issue token, and audit the login attempt."""
        raise NotImplementedError

    def current_user(self, token: str) -> UserRecord:
        """TODO: resolve the current user and enforce active-account policy."""
        raise NotImplementedError


class PredictionController:
    def list_students(self, filters: StudentListFilters) -> list[PredictionRecord]:
        """TODO: return paginated predictions for an advisor's review queue."""
        raise NotImplementedError

    def get_student(self, prediction_id: int) -> PredictionRecord:
        """TODO: return a prediction detail and safe display metadata."""
        raise NotImplementedError


class BriefController:
    def generate(self, prediction_id: int, actor: UserRecord) -> BriefRecord:
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


class BatchImportController:
    def import_prediction_batch(self, manifest_path: Path) -> BatchImportReport:
        """TODO: authorize an ML batch import and invoke its import service."""
        raise NotImplementedError


class LearnGuardApi:
    """Owns API route wiring and application lifecycle."""

    def create_app(self) -> FastAPI:
        """Build the FastAPI app via the module-level factory."""
        return create_app()
