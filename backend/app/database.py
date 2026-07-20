"""
SQLAlchemy async database engine and session factory.

Provides async database connectivity with connection pooling,
health checks, and session management for FastAPI dependency injection.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


def get_engine() -> AsyncEngine:
    """Get or create the global async engine."""
    global _engine
    if _engine is None:
        # Always use NullPool: Supabase's transaction-mode pooler already handles
        # connection pooling, so stacking client-side QueuePool on top causes
        # connection issues. NullPool rejects pool_size/max_overflow/pool_timeout,
        # so those are intentionally omitted.
        # statement_cache_size=0 is required with the transaction-mode pooler:
        # asyncpg's prepared-statement cache conflicts with Supavisor reusing
        # backends across transactions, causing "prepared statement does not
        # exist" errors under concurrency.
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_recycle=3600,  # Recycle connections every hour
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0},
        )
        logger.info("database.engine_created", poolclass="NullPool")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the global session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        logger.info("database.session_factory_created")
    return _session_factory


async def init_db() -> None:
    """Initialize database connection and create tables if needed."""
    engine = get_engine()
    # Import all models to register them with Base.metadata
    from app.models import (  # noqa: F401
        alert,
        credential,
        execution,
        hitl,
        integration,
        monitor,
        workspace,
    )

    async with engine.begin() as conn:
        # Create tables if they don't exist (Alembic handles migrations in production)
        if settings.ENVIRONMENT != "production":
            await conn.run_sync(Base.metadata.create_all)
            logger.info("database.tables_created")


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database.engine_disposed")


async def check_db_connection() -> bool:
    """Check if database connection is healthy."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("database.health_check_failed", error=str(e))
        return False


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session for use outside of FastAPI request lifecycle.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database session.

    Usage in route:
        async def route(session: AsyncSession = Depends(get_db_session)):
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
