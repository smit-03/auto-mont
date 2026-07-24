"""
Alert schemas - Pydantic models for alert history and acknowledgment.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    integration_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    monitor_id: uuid.UUID | None
    credential_id: uuid.UUID | None
    severity: Literal["info", "warning", "critical"]
    category: Literal["auth", "api", "logic", "schema", "infra", "ai", "silent", "unknown"]
    title: str
    description: str
    root_cause: str | None
    suggested_fix: str | None
    auto_remediated: bool
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None


class AlertAcknowledge(BaseModel):
    model_config = ConfigDict(strict=True)

    acknowledged_by: str | None = None


class AlertResolve(BaseModel):
    model_config = ConfigDict(strict=True)

    pass


class AlertListParams(BaseModel):
    model_config = ConfigDict(strict=True)

    severity: Literal["info", "warning", "critical"] | None = None
    category: Literal["auth", "api", "logic", "schema", "infra", "ai", "silent", "unknown"] | None = None
    resolved: bool | None = None
    after: str | None = None  # Cursor for pagination
    limit: int = 50
