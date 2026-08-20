"""
Monitor schemas - Pydantic models for heartbeat monitor CRUD.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MonitorBase(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    monitor_type: Literal["heartbeat", "outcome", "cron"]


class MonitorCreate(MonitorBase):
    integration_id: uuid.UUID | None = None
    # Platform-native workflow id (n8n's `workflowId`, not a WRP UUID). Required
    # in practice for outcome and schema monitors, which assert against one
    # workflow's output; meaningless for heartbeat and cron monitors, which are
    # driven by an inbound ping.
    workflow_id: str | None = Field(None, min_length=1, max_length=255)
    grace_period_s: int = Field(default=300, ge=30, le=3600)
    expected_cron: str | None = Field(
        None, pattern=r"^[\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+ [\d*,\-/]+$"
    )
    expected_outcome: dict | None = None

    @model_validator(mode="after")
    def _outcome_monitors_must_name_a_workflow(self) -> "MonitorCreate":
        """
        Reject an outcome monitor with no workflow scope.

        Assertions are evaluated by matching a polled execution's workflow_id
        against this column. An outcome monitor without one matches nothing, so
        it would sit in the UI looking configured while silently watching
        nothing — the exact failure class this product exists to catch, and the
        reason workflow_id is a validated column rather than a JSONB key.
        """
        if self.monitor_type == "outcome" and not self.workflow_id:
            raise ValueError("workflow_id is required for outcome monitors")
        return self


class MonitorUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    workflow_id: str | None = Field(None, min_length=1, max_length=255)
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
    workflow_id: str | None
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
