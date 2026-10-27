"""M7 repository interfaces. TODO: implement queries and transactions."""

from typing import Protocol

from learnguard_backend.db.models import (
    AuditLogRecord,
    BriefRecord,
    DecisionRecord,
    ModelVersionRecord,
    PredictionRecord,
    UserRecord,
)


class PredictionRepository(Protocol):
    def get(self, prediction_id: int) -> PredictionRecord | None: ...
    def list_latest(self, *, limit: int, offset: int) -> list[PredictionRecord]: ...
    def save_many(self, predictions: list[PredictionRecord]) -> int: ...


class ModelRepository(Protocol):
    def get(self, model_version: str) -> ModelVersionRecord | None: ...
    def save(self, model: ModelVersionRecord) -> None: ...


class BriefRepository(Protocol):
    def get(self, brief_id: int) -> BriefRecord | None: ...
    def save_new_version(self, brief: BriefRecord) -> BriefRecord: ...


class DecisionRepository(Protocol):
    def save(self, decision: DecisionRecord) -> DecisionRecord: ...


class AuditLogRepository(Protocol):
    def append(self, event: AuditLogRecord) -> None: ...


class UserRepository(Protocol):
    def get_by_email(self, email: str) -> UserRecord | None: ...
