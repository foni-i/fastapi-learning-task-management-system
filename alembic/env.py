"""Alembic environment configured through application settings."""

from logging.config import fileConfig

from alembic.config import Config
from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import get_settings
from app.db.session import DatabaseConfigurationError
from app.models import Project

config: Config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Project.metadata


def get_database_url() -> str:
    """Return the configured URL without storing it in Alembic configuration."""

    database_url = get_settings().database_url
    if database_url is None:
        raise DatabaseConfigurationError("STMS_DATABASE_URL is not configured")

    return database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Configure SQL generation without creating an Engine or connection."""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a synchronous PostgreSQL connection when requested."""

    connectable = create_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
