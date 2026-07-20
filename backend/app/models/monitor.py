"""
Monitor model - Heartbeat / dead-man's switch monitors.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.integration import Integration
    from app.models.workspace import Workspace


class Monitor(Base):
    """Heartbeat / dead-man's switch monitors."""

    __tablename__ = "monitors"

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
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # heartbeat | outcome | cron | schema
    ping_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    expected_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    grace_period_s: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
        server_default="300",
    )
    expected_outcome: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )  # healthy | degraded | failing
    alert_on_miss: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="monitors",
        lazy="selectin",
    )
    integration: Mapped[Optional["Integration"]] = relationship(
        "Integration",
        back_populates="monitors",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Monitor(id={self.id}, name={self.name}, type={self.monitor_type}, status={self.last_status})>"
