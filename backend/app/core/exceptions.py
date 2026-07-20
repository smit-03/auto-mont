"""
Custom exception hierarchy for the Workflow Reliability Platform.

All exceptions inherit from WRPException which provides structured error details.
"""

from typing import Any


class WRPException(Exception):
    """Base exception for all WRP errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "WRP_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}


class AuthenticationError(WRPException):
    """Raised when authentication fails."""

    def __init__(
        self, message: str = "Authentication required", details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code="UNAUTHORIZED",
            status_code=401,
            details=details,
        )


class AuthorizationError(WRPException):
    """Raised when authorization fails (authenticated but not permitted)."""

    def __init__(
        self, message: str = "Insufficient permissions", details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            error_code="FORBIDDEN",
            status_code=403,
            details=details,
        )


class NotFoundError(WRPException):
    """Raised when a resource is not found."""

    def __init__(self, message: str = "Resource not found", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="NOT_FOUND",
            status_code=404,
            details=details,
        )


class ValidationError(WRPException):
    """Raised when request validation fails."""

    def __init__(self, message: str = "Validation failed", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="VALIDATION_ERROR",
            status_code=422,
            details=details,
        )


class RateLimitError(WRPException):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int = 60,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="RATE_LIMITED",
            status_code=429,
            details=details or {"retry_after": retry_after},
        )
        self.retry_after = retry_after


class ExternalServiceError(WRPException):
    """Raised when an external service call fails."""

    def __init__(
        self,
        message: str = "External service error",
        service: str = "unknown",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="EXTERNAL_SERVICE_ERROR",
            status_code=502,
            details=details or {"service": service},
        )
        self.service = service


class ConfigurationError(WRPException):
    """Raised when configuration is invalid."""

    def __init__(self, message: str = "Configuration error", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="CONFIGURATION_ERROR",
            status_code=500,
            details=details,
        )


class EncryptionError(WRPException):
    """Raised when encryption/decryption fails."""

    def __init__(self, message: str = "Encryption error", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="ENCRYPTION_ERROR",
            status_code=500,
            details=details,
        )


class IntegrationError(WRPException):
    """Raised when platform integration fails."""

    def __init__(
        self,
        message: str = "Integration error",
        platform: str = "unknown",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="INTEGRATION_ERROR",
            status_code=502,
            details=details or {"platform": platform},
        )
        self.platform = platform


class CredentialError(WRPException):
    """Raised when credential operations fail."""

    def __init__(self, message: str = "Credential error", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="CREDENTIAL_ERROR",
            status_code=400,
            details=details,
        )


class DiagnosticError(WRPException):
    """Raised when diagnostic engine fails."""

    def __init__(self, message: str = "Diagnostic error", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="DIAGNOSTIC_ERROR",
            status_code=500,
            details=details,
        )


class RemediationError(WRPException):
    """Raised when remediation action fails."""

    def __init__(self, message: str = "Remediation error", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="REMEDIATION_ERROR",
            status_code=500,
            details=details,
        )
