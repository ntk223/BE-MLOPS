"""Alembic environment for the LearnGuard backend.

The database URL comes from DATABASE_URL (via learnguard_backend.settings),
falling back to the alembic.ini url attribute — no secrets in config files.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from learnguard_backend.db.entities import Base
from learnguard_backend.settings import BackendSettings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = BackendSettings.from_environment()
    if settings.database_url:
        return settings.database_url
    return str(config.get_main_option("url") or "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to the database)."""
    from sqlalchemy import create_engine

    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
