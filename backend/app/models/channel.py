"""
Notification channel model — per-workspace alert delivery destinations.

Alerts were previously delivered to a single global Slack webhook read from
application settings, so every workspace's alerts landed in one channel. The
data layer was properly multi-tenant — every query scoped by workspace_id,
credentials cryptographically bound to their tenant — and then the notification
layer took that isolated data and broadcast all of it to one destination.

Multi-tenancy is an end-to-end property, not a storage property.

The destination (a Slack webhook URL) is a credential: anyone holding it can
post into that customer's channel. It is encrypted at rest with the same
per-workspace AES-256-GCM scheme used for integration API keys and OAuth
tokens, never stored in plaintext.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Ordering used to evaluate a channel's min_severity threshold.
SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


class NotificationChannel(Base):
    """A workspace-scoped destination for alert delivery."""

    __tablename__ = "notification_channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # slack | email | pagerduty  (only slack is delivered today)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Encrypted webhook URL / destination. Treated as a credential.
    destination_enc: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    # Safe-to-display fragment, e.g. the trailing path segment of the webhook.
    destination_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    min_severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="warning",
        server_default="warning",
    )  # info | warning | critical

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # No ORM relationship to Workspace on purpose: nothing needs to traverse it,
    # and adding one is how the eager-loading problem started.
    __table_args__ = (
        Index(
            "idx_channels_workspace_active",
            "workspace_id",
            "enabled",
            postgresql_where=deleted_at.is_(None),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationChannel(id={self.id}, type={self.channel_type}, "
            f"name={self.display_name})>"
        )
