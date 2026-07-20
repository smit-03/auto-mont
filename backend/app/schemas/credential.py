"""
Credential schemas - Pydantic models for OAuth credential vault.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CredentialBase(BaseModel):
    model_config = ConfigDict(strict=True)

    display_name: str = Field(..., min_length=1, max_length=255)
    provider: Literal["google", "microsoft", "github", "slack", "custom"]


class CredentialCreate(CredentialBase):
    integration_id: uuid.UUID | None = None
    token: str = Field(..., min_length=10)
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] | None = None
    gcp_status: Literal["testing", "production"] | None = None


class CredentialUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    display_name: str | None = Field(None, min_length=1, max_length=255)


class CredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    integration_id: uuid.UUID | None
    provider: str
    display_name: str
    scopes: list[str] | None
    gcp_status: str | None
    expires_at: datetime | None
    last_verified_at: datetime | None
    last_error: str | None
    status: str
    created_at: datetime

    # Computed fields for UI
    hours_until_expiry: float | None = None
    is_expiring_soon: bool = False


class CredentialReadPublic(BaseModel):
    """Public credential info without sensitive data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    display_name: str
    status: str
    created_at: datetime
