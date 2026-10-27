"""M6 API application and route orchestration contracts."""

from typing import Any
from pathlib import Path

from learnguard_backend.api.schemas import DecisionRequest, StudentListFilters
from learnguard_backend.db.models import BriefRecord, PredictionRecord, UserRecord
from learnguard_backend.services.batch_import import BatchImportReport


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


class BatchImportController:
    def import_prediction_batch(self, manifest_path: Path) -> BatchImportReport:
        """TODO: authorize an ML batch import and invoke its import service."""
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

    def create_app(self) -> Any:
        """TODO: build the FastAPI app, middleware, handlers, and routers."""
        raise NotImplementedError

    def health(self) -> dict[str, str]:
        """TODO: report application and dependency health."""
        raise NotImplementedError


def create_app() -> Any:
    """TODO factory used by the ASGI server."""
    raise NotImplementedError
