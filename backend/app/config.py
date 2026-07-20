"""
Application configuration using Pydantic Settings.

All environment variables are defined here with validation and defaults.
"""

from functools import lru_cache

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # Application
    # =========================================================================
    APP_NAME: str = "Workflow Reliability Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(
        default="development", pattern="^(development|staging|production|test)$"
    )
    DEBUG: bool = False

    # =========================================================================
    # Database
    # =========================================================================
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/wrp_dev",
        description="Async PostgreSQL connection URL",
    )
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=50)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=100)
    DATABASE_POOL_TIMEOUT: int = Field(default=30, ge=1, le=300)

    # =========================================================================
    # Redis / Celery
    # =========================================================================
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for Celery broker and cache",
    )
    CELERY_BROKER_URL: str | None = None  # Defaults to REDIS_URL
    CELERY_RESULT_BACKEND: str | None = None  # Defaults to REDIS_URL
    CELERY_TASK_ALWAYS_EAGER: bool = Field(
        default=False, description="Run tasks synchronously for testing"
    )

    # =========================================================================
    # Security
    # =========================================================================
    CREDENTIAL_MASTER_KEY: str = Field(
        default="",
        description="32-byte hex key for AES-256-GCM encryption (generate with: openssl rand -hex 32)",
        min_length=64,
        max_length=64,
    )
    JWT_SECRET_KEY: str = Field(
        default="",
        description="Clerk JWT signing key for token validation",
        min_length=32,
    )
    JWT_ALGORITHM: str = "RS256"
    JWT_AUDIENCE: str | None = None
    JWT_ISSUER: str | None = Field(
        default=None,
        description="Clerk JWT issuer URL (e.g. https://<slug>.clerk.accounts.dev)",
    )

    # =========================================================================
    # Clerk Authentication
    # =========================================================================
    CLERK_SECRET_KEY: str = Field(default="", description="Clerk secret key (sk_...)")
    CLERK_PUBLISHABLE_KEY: str = Field(default="", description="Clerk publishable key (pk_...)")
    CLERK_WEBHOOK_SECRET: str = Field(default="", description="Clerk webhook secret for user sync")

    # =========================================================================
    # n8n (Phase 1 — single default integration for local/dev polling)
    # =========================================================================
    N8N_BASE_URL: str | None = Field(default=None, description="Default n8n instance base URL")
    N8N_API_KEY: str | None = Field(default=None, description="Default n8n API key for polling")

    # =========================================================================
    # AI / LLM Services
    # =========================================================================
    ANTHROPIC_API_KEY: str | None = Field(
        default=None, description="Anthropic API key for LLM Judge"
    )
    OPENAI_API_KEY: str | None = Field(default=None, description="OpenAI API key for embeddings")
    ENABLE_LLM_JUDGE: bool = Field(default=False, description="Enable LLM Judge diagnostic tier")
    LLM_JUDGE_DAILY_BUDGET_USD: float = Field(default=10.0, ge=0.0, le=1000.0)
    LLM_JUDGE_MODEL: str = Field(default="claude-sonnet-4-6")

    # =========================================================================
    # Notifications
    # =========================================================================
    RESEND_API_KEY: str | None = Field(
        default=None, description="Resend API key for email delivery"
    )
    SLACK_WEBHOOK_URL: str | None = Field(default=None, description="Slack incoming webhook URL")
    SLACK_SIGNING_SECRET: str | None = Field(
        default=None, description="Slack signing secret for HITL callbacks"
    )

    # =========================================================================
    # Object Storage (Phase 2+)
    # =========================================================================
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET_NAME: str = "wrp-traces"

    # =========================================================================
    # Feature Flags & Limits
    # =========================================================================
    MAX_POLL_CONCURRENCY: int = Field(default=10, ge=1, le=100)
    DEFAULT_POLL_INTERVAL_SECONDS: int = Field(default=60, ge=30, le=3600)
    ALERT_DEDUP_WINDOW_SECONDS: int = Field(default=3600, ge=60, le=86400)
    HEARTBEAT_CHECK_INTERVAL_SECONDS: int = Field(default=60, ge=10, le=3600)
    CREDENTIAL_CHECK_INTERVAL_SECONDS: int = Field(default=3600, ge=300, le=86400)

    # =========================================================================
    # CORS
    # =========================================================================
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="Allowed CORS origins (comma-separated)",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> str:
        """Parse comma-separated CORS_ORIGINS from .env to comma-separated string."""
        if isinstance(v, list):
            return ",".join(v)
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS_ORIGINS as a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def set_celery_broker(cls, v: str | None, info: ValidationInfo) -> str:
        if v:
            return v
        return str(info.data.get("REDIS_URL", "redis://localhost:6379/0"))

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def set_celery_backend(cls, v: str | None, info: ValidationInfo) -> str:
        if v:
            return v
        return str(info.data.get("REDIS_URL", "redis://localhost:6379/0"))

    @field_validator("CREDENTIAL_MASTER_KEY", mode="after")
    @classmethod
    def validate_master_key(cls, v: str) -> str:
        if not v:
            if cls.model_fields["ENVIRONMENT"].default == "development":
                # Allow empty in dev, will be overridden by test fixtures
                return "0" * 64
            raise ValueError(
                "CREDENTIAL_MASTER_KEY must be set (generate with: openssl rand -hex 32)"
            )
        try:
            bytes.fromhex(v)
        except ValueError as err:
            raise ValueError("CREDENTIAL_MASTER_KEY must be valid hex string") from err
        if len(v) != 64:
            raise ValueError("CREDENTIAL_MASTER_KEY must be 64 hex characters (32 bytes)")
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Export for convenience
settings = get_settings()
