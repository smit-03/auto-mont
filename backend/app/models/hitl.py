"""
HITL Request model - Human-in-the-loop approval queue.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

if TYPE_CHECKING:
    pass


class HITLRequest(Base):
    """Human-in-the-loop approval queue."""

    __tablename__ = "hitl_requests"

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
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alerts.id", ondelete="SET NULL"),
        nullable=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
    )
    state_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    proposed_fix: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )  # pending | approved | rejected | expired
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("idx_hitl_workspace_status", "workspace_id", "status"),
        Index("idx_hitl_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<HITLRequest(id={self.id}, status={self.status}, alert_id={self.alert_id})>"
