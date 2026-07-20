"""
Structured logging configuration using structlog.

Provides consistent JSON logging with context binding for traceability.
"""

import logging
import sys
from typing import Any, cast

import structlog
from structlog.stdlib import LoggerFactory

from app.config import settings


def configure_logging() -> None:
    """Configure structlog for the application."""

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer()
            if settings.ENVIRONMENT == "production"
            else structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set log levels for noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("redis").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


class LogContext:
    """Context manager for binding context to logs."""

    def __init__(self, **kwargs: Any):
        self.context = kwargs
        self.tokens: list[tuple[str, Any]] = []

    def __enter__(self) -> "LogContext":
        for key, value in self.context.items():
            token = structlog.contextvars.bind_contextvars(**{key: value})
            self.tokens.append((key, token))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        for key, _token in self.tokens:
            structlog.contextvars.unbind_contextvars(key)


def bind_context(**kwargs: Any) -> None:
    """Bind context variables to current logger context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_context(*keys: str) -> None:
    """Unbind context variables from current logger context."""
    structlog.contextvars.unbind_contextvars(*keys)


def clear_context() -> None:
    """Clear all context variables."""
    structlog.contextvars.clear_contextvars()


# Convenience functions for common log patterns
def log_request(
    logger: structlog.stdlib.BoundLogger, request_id: str, method: str, path: str, **kwargs: Any
) -> None:
    """Log incoming request."""
    logger.info(
        "request.started",
        request_id=request_id,
        method=method,
        path=path,
        **kwargs,
    )


def log_response(
    logger: structlog.stdlib.BoundLogger,
    request_id: str,
    status_code: int,
    duration_ms: float,
    **kwargs: Any,
) -> None:
    """Log completed request."""
    logger.info(
        "request.completed",
        request_id=request_id,
        status_code=status_code,
        duration_ms=duration_ms,
        **kwargs,
    )


def log_error(
    logger: structlog.stdlib.BoundLogger,
    error: Exception,
    message: str = "Error occurred",
    **kwargs: Any,
) -> None:
    """Log an error with full context."""
    logger.error(
        message,
        error_type=type(error).__name__,
        error_message=str(error),
        **kwargs,
    )


def log_db_query(
    logger: structlog.stdlib.BoundLogger,
    query: str,
    duration_ms: float,
    **kwargs: Any,
) -> None:
    """Log database query."""
    logger.debug(
        "db.query",
        query=query,
        duration_ms=duration_ms,
        **kwargs,
    )


def log_external_call(
    logger: structlog.stdlib.BoundLogger,
    service: str,
    method: str,
    url: str,
    status_code: int,
    duration_ms: float,
    **kwargs: Any,
) -> None:
    """Log external API call."""
    logger.info(
        "external.call",
        service=service,
        method=method,
        url=url,
        status_code=status_code,
        duration_ms=duration_ms,
        **kwargs,
    )
