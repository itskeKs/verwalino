"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models.base import Base
from app.core.config import settings
from app.core.declaration_test_database import (
    is_disposable_database_candidate,
    require_disposable_database_settings,
)

# Import all models so their metadata is registered
import app.models  # noqa: F401

config = context.config

# Drive ordinary migrations from app settings. The disposable declaration-test
# database is an explicit exception: it validates both raw environment values and
# their effective Settings counterparts before supplying the sync URL.
if (
    config.attributes.get("hv_declaration_test_database")
    or is_disposable_database_candidate(os.environ.get("HV_DATABASE_URL_SYNC"))
    or is_disposable_database_candidate(settings.database_url_sync)
):
    _, _, _validated_sync_url = require_disposable_database_settings(settings)
    config.set_main_option("sqlalchemy.url", _validated_sync_url)
else:
    config.set_main_option("sqlalchemy.url", settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
