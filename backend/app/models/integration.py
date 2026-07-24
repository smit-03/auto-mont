"""
Integration model - Platform connections (n8n, Make, Zapier, etc.).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.execution import Execution
    from app.models.monitor import Monitor
    from app.models.workspace import Workspace


class Integration(Base):
    """Platform integration (n8n, Make, Zapier, LangGraph, etc.)."""

    __tablename__ = "integrations"

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
    platform: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # n8n | make | zapier | langgraph | crewai | custom
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    api_key_hint: Mapped[str | None] = mapped_column(String(10), nullable=True)
    polling_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    poll_interval_s: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default="60",
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )  # active | paused | error
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="integrations",
        lazy="selectin",
    )
    executions: Mapped[list["Execution"]] = relationship(
        "Execution",
        back_populates="integration",
        lazy="selectin",
    )
    monitors: Mapped[list["Monitor"]] = relationship(
        "Monitor",
        back_populates="integration",
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_integrations_workspace_active",
            "workspace_id",
            "deleted_at",
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "uq_integrations_workspace_platform_name_active",
            "workspace_id",
            "platform",
            "display_name",
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
    )

    def __repr__(self) -> str:
        return f"<Integration(id={self.id}, platform={self.platform}, name={self.display_name})>"
