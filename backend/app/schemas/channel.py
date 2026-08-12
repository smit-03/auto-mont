"""
Notification channel schemas.

The destination URL is write-only: accepted on create, never returned. Only a
non-sensitive hint is exposed for the UI, matching how integration API keys are
handled.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    channel_type: Literal["slack"] = "slack"
    display_name: str = Field(..., min_length=1, max_length=255)
    destination: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        description="Slack incoming webhook URL. Stored encrypted; never returned.",
    )
    min_severity: Literal["info", "warning", "critical"] = "warning"


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    display_name: str | None = Field(None, min_length=1, max_length=255)
    enabled: bool | None = None
    min_severity: Literal["info", "warning", "critical"] | None = None


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    channel_type: str
    display_name: str
    destination_hint: str | None
    enabled: bool
    min_severity: str
    created_at: datetime


class ChannelTestResult(BaseModel):
    delivered: bool
    error_message: str | None = None
