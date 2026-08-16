"""
Integration schemas - Pydantic models for platform connection management.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntegrationBase(BaseModel):
    model_config = ConfigDict(strict=True)

    display_name: str = Field(..., min_length=1, max_length=255)
    platform: Literal["n8n", "make", "zapier", "langgraph", "crewai", "custom"]


class IntegrationCreate(IntegrationBase):
    base_url: str | None = None
    api_key: str = Field(..., min_length=10)
    poll_interval_s: int = Field(default=60, ge=30, le=3600)


class IntegrationUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    display_name: str | None = Field(None, min_length=1, max_length=255)
    polling_enabled: bool | None = None
    poll_interval_s: int | None = Field(None, ge=30, le=3600)


class IntegrationTest(BaseModel):
    model_config = ConfigDict(strict=True)

    integration_id: uuid.UUID


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    platform: str
    display_name: str
    base_url: str | None
    api_key_hint: str | None
    polling_enabled: bool
    poll_interval_s: int
    last_polled_at: datetime | None
    status: str
    config: dict
    created_at: datetime
    deleted_at: datetime | None = None


class WorkflowSummary(BaseModel):
    """
    One workflow seen on an integration, derived from ingested executions.

    Distinct workflow_ids from the executions table rather than a live call to
    the platform's /workflows API: it needs no credentials, no network round
    trip, and cannot fail while the platform is down. The tradeoff is that a
    workflow which has never run does not appear — acceptable, because a monitor
    on a workflow with no execution history has nothing to assert against yet.
    """

    model_config = ConfigDict(from_attributes=True)

    workflow_id: str
    workflow_name: str | None
    last_seen_at: datetime


class IntegrationTestResult(BaseModel):
    connected: bool
    error_message: str | None = None
    workflows_count: int | None = None
