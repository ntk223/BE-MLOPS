"""M7 session lifecycle contract. TODO: configure SQLAlchemy engine/session."""

from contextlib import AbstractContextManager
from typing import Any, Protocol


class SessionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Any]: ...


def create_session_factory(database_url: str) -> SessionFactory:
    """TODO: construct an engine and scoped session factory."""
    raise NotImplementedError


def get_session() -> AbstractContextManager[Any]:
    """TODO: provide a request-scoped database session."""
    raise NotImplementedError
