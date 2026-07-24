"""
Workspace model - Multi-tenant workspace/organization.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.credential import Credential
    from app.models.execution import Execution
    from app.models.integration import Integration
    from app.models.monitor import Monitor


class Workspace(Base):
    """Multi-tenant workspace (organization)."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    clerk_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    plan: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="developer",
        server_default="developer",
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
    integrations: Mapped[list["Integration"]] = relationship(
        "Integration",
        back_populates="workspace",
        lazy="selectin",
    )
    executions: Mapped[list["Execution"]] = relationship(
        "Execution",
        back_populates="workspace",
        lazy="selectin",
    )
    monitors: Mapped[list["Monitor"]] = relationship(
        "Monitor",
        back_populates="workspace",
        lazy="selectin",
    )
    credentials: Mapped[list["Credential"]] = relationship(
        "Credential",
        back_populates="workspace",
        lazy="selectin",
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert",
        back_populates="workspace",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Workspace(id={self.id}, name={self.name}, slug={self.slug})>"
