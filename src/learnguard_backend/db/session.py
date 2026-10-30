"""M7 engine/session lifecycle (DB_CONTRACT.md §3).

The engine is created lazily from ``BackendSettings.database_url`` and cached
per process; ``get_session`` yields request-scoped sessions that commit on
success and roll back on error.
"""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from learnguard_backend.settings import BackendSettings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_cached_url: str | None = None


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Construct an engine and a bound session factory."""
    if not database_url:
        raise ValueError("database_url is required (set DATABASE_URL).")
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_engine(settings: BackendSettings | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use.

    The cache is keyed by URL: tests may configure different targets in one
    process, and a re-configuration must not reuse a stale engine.
    """
    global _engine, _session_factory, _cached_url
    resolved = settings or BackendSettings.from_environment()
    if _engine is None or _cached_url != resolved.database_url:
        _session_factory = create_session_factory(resolved.database_url)
        _engine = _session_factory.kw["bind"]
        _cached_url = resolved.database_url
    return _engine


def get_session_factory(settings: BackendSettings | None = None) -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use."""
    get_engine(settings)
    assert _session_factory is not None
    return _session_factory


def get_session(settings: BackendSettings | None = None) -> Iterator[Any]:
    """Provide a request-scoped database session."""
    factory = get_session_factory(settings)
    with factory() as session:
        yield session
