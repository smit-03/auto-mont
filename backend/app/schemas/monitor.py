"""
Monitor schemas - Pydantic models for heartbeat monitor CRUD.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MonitorBase(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    monitor_type: Literal["heartbeat", "outcome", "cron", "schema"]


class MonitorCreate(MonitorBase):
    integration_id: uuid.UUID | None = None
    grace_period_s: int = Field(default=300, ge=30, le=3600)
    expected_cron: str | None = Field(
        None, pattern=r"^[\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+$"
    )
    expected_outcome: dict | None = None


class MonitorUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    grace_period_s: int | None = Field(None, ge=30, le=3600)
    expected_cron: str | None = Field(
        None, pattern=r"^[\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+$"
    )
    expected_outcome: dict | None = None
    alert_on_miss: bool | None = None


class MonitorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    integration_id: uuid.UUID | None
    name: str
    description: str | None
    monitor_type: str
    ping_url: str | None
    grace_period_s: int
    last_ping_at: datetime | None
    last_status: str
    alert_on_miss: bool
    created_at: datetime


class MonitorStatus(BaseModel):
    model_config = ConfigDict(strict=True)

    status: Literal["healthy", "degraded", "failing"]
    last_ping_at: datetime | None = None
    next_expected: datetime | None = None
    missed_count: int = 0
