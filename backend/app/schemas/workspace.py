"""
Workspace schemas - Pydantic models for workspace CRUD.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceBase(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = Field(..., min_length=1, max_length=255)


class WorkspaceCreate(WorkspaceBase):
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str | None = Field(None, min_length=1, max_length=255)


class WorkspaceRead(WorkspaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    plan: str = "developer"
    created_at: datetime
    deleted_at: datetime | None = None


class WorkspaceMe(BaseModel):
    """Response for GET /api/v1/workspaces/me endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    plan: str
