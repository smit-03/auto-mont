"""
FastAPI Application Factory.

Creates and configures the FastAPI application with all middleware,
routers, exception handlers, and lifecycle events.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    WRPException,
)
from app.core.logging import configure_logging, get_logger

# Metrics
REQUEST_COUNT = Counter(
    "wrp_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "wrp_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect Prometheus metrics for HTTP requests."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        method = request.method
        endpoint = request.url.path

        with REQUEST_LATENCY.labels(method=method, endpoint=endpoint).time():
            response = await call_next(request)

        REQUEST_COUNT.labels(
            method=method,
            endpoint=endpoint,
            status_code=response.status_code,
        ).inc()

        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    logger = get_logger(__name__)

    # Startup
    logger.info(
        "application.starting", environment=settings.ENVIRONMENT, version=settings.APP_VERSION
    )
    configure_logging()

    # Initialize database connection pool (handled by SQLAlchemy engine)
    from app.database import init_db

    await init_db()

    logger.info("application.started")

    yield

    # Shutdown
    logger.info("application.shutting_down")
    from app.database import close_db

    await close_db()
    logger.info("application.shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Workflow Reliability Platform API",
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
        openapi_url="/openapi.json" if settings.ENVIRONMENT != "production" else None,
        lifespan=lifespan,
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Metrics Middleware
    app.add_middleware(MetricsMiddleware)

    # Exception Handlers
    @app.exception_handler(WRPException)
    async def wrp_exception_handler(request: Request, exc: WRPException) -> JSONResponse:
        logger = get_logger(__name__)
        logger.warning(
            "exception.wrp",
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(AuthenticationError)
    async def auth_exception_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": {"code": "UNAUTHORIZED", "message": exc.message}},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def authz_exception_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": "FORBIDDEN", "message": exc.message}},
        )

    @app.exception_handler(NotFoundError)
    async def not_found_exception_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": {"code": "NOT_FOUND", "message": exc.message}},
        )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_exception_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"code": "RATE_LIMITED", "message": exc.message}},
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(ExternalServiceError)
    async def external_service_exception_handler(
        request: Request, exc: ExternalServiceError
    ) -> JSONResponse:
        logger = get_logger(__name__)
        logger.error(
            "exception.external_service",
            service=exc.service,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": {"code": "EXTERNAL_SERVICE_ERROR", "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger = get_logger(__name__)
        logger.exception(
            "exception.unhandled",
            error_type=type(exc).__name__,
            message=str(exc),
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}
            },
        )

    # Health Check Endpoint
    @app.get("/health", tags=["Health"])
    async def health_check() -> JSONResponse:
        """Health check endpoint for load balancers and monitoring."""
        from app.core.redis import check_redis_connection
        from app.database import check_db_connection

        db_ok = await check_db_connection()
        redis_ok = await check_redis_connection()

        status_ok = db_ok and redis_ok

        return JSONResponse(
            status_code=status.HTTP_200_OK if status_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "healthy" if status_ok else "unhealthy",
                "checks": {
                    "database": "ok" if db_ok else "failed",
                    "redis": "ok" if redis_ok else "failed",
                },
                "version": settings.APP_VERSION,
            },
        )

    # Prometheus Metrics Endpoint
    @app.get("/metrics", tags=["Observability"])
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        from fastapi.responses import Response

        return Response(content=generate_latest(), media_type="text/plain")

    # Public Heartbeat Ping Endpoint (no auth required)

    @app.post("/ping/{ping_token}", tags=["Heartbeat"], include_in_schema=False)
    async def heartbeat_ping(ping_token: str) -> JSONResponse:
        """
        Public heartbeat ping endpoint.

        Workflows call this endpoint to signal they are alive.
        """
        from sqlalchemy import select

        from app.database import get_session
        from app.models.monitor import Monitor

        async with get_session() as session:
            result = await session.execute(
                select(Monitor).where(
                    Monitor.ping_url.contains(ping_token),
                    Monitor.deleted_at.is_(None),
                )
            )
            monitor = result.scalar_one_or_none()

            if not monitor:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Monitor not found or token invalid"},
                )

            from datetime import datetime

            monitor.last_ping_at = datetime.now(UTC)
            monitor.last_status = "healthy"

            await session.commit()

            return JSONResponse(
                status_code=200,
                content={
                    "status": "received",
                    "monitor_id": str(monitor.id),
                },
            )

    # API Router
    from app.api.v1.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_config=None,  # We use structlog
    )
