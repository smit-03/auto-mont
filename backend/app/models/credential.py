"""
Credential model - OAuth token lifecycle tracking.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ARRAY, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.integration import Integration
    from app.models.workspace import Workspace


class Credential(Base):
    """OAuth credential lifecycle tracking."""

    __tablename__ = "credentials"

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
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # google | microsoft | github | slack | etc.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    refresh_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)), nullable=True)
    gcp_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # testing | production
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )  # active | expiring | expired | error
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="credentials",
        lazy="selectin",
    )
    integration: Mapped[Optional["Integration"]] = relationship(
        "Integration",
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_credentials_expiry",
            "workspace_id",
            "expires_at",
            postgresql_where=(deleted_at.is_(None)) & (func.lower(status) != "expired"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Credential(id={self.id}, provider={self.provider}, name={self.display_name}, status={self.status})>"
