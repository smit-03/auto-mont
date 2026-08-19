"""
Notification channel schemas.

The destination URL is write-only: accepted on create, never returned. Only a
non-sensitive hint is exposed for the UI, matching how integration API keys are
handled.
"""

import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChannelCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    channel_type: Literal["slack", "email"] = "slack"
    display_name: str = Field(..., min_length=1, max_length=255)
    # Lower bound is 6, the shortest possible valid email ("a@b.cd"). The
    # per-type validator below is what actually enforces shape.
    destination: str = Field(
        ...,
        min_length=6,
        max_length=2048,
        description=(
            "Slack incoming webhook URL, or email address for email channels. "
            "Stored encrypted; never returned."
        ),
    )
    min_severity: Literal["info", "warning", "critical"] = "warning"

    @model_validator(mode="after")
    def _destination_must_match_channel_type(self) -> "ChannelCreate":
        """
        Reject a destination that does not match its channel type.

        Nothing downstream re-checks this. The dispatcher posts a slack
        destination to httpx and hands an email destination to Resend's `to`
        field, so a swapped pair is only discovered when a real alert fails to
        deliver — precisely the moment the channel needed to work.
        """
        if self.channel_type == "email":
            try:
                validate_email(self.destination, check_deliverability=False)
            except EmailNotValidError as exc:
                raise ValueError(f"Not a valid email address: {exc}") from exc
            return self

        parsed = urlparse(self.destination)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Slack webhook must be an https:// URL")
        if parsed.netloc != "hooks.slack.com" and not parsed.netloc.endswith(".slack.com"):
            raise ValueError("Slack webhook must be hosted on slack.com")
        return self


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
