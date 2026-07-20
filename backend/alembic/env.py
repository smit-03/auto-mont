"""
Alembic environment configuration for async SQLAlchemy.

This module configures the migration environment to work with async PostgreSQL.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import settings
from app.database import Base

# Configure logging
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# Set target metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:
    """Run migrations synchronously with a connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        run_kwargs={"exec_name": "alembic"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
