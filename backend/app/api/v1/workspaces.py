"""
Workspace endpoints - CRUD operations for workspaces.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead, WorkspaceUpdate

router = APIRouter()


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_db_session),
) -> WorkspaceRead:
    """Create a new workspace."""
    # Check if slug exists
    result = await session.execute(select(Workspace).where(Workspace.slug == payload.slug))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workspace slug '{payload.slug}' already exists",
        )

    workspace = Workspace(
        name=payload.name,
        slug=payload.slug,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    return WorkspaceRead.model_validate(workspace)


@router.get("/me", response_model=WorkspaceRead)
async def get_current_workspace_info(
    workspace: Workspace = Depends(get_current_workspace),
) -> WorkspaceRead:
    """Get current workspace details."""
    return WorkspaceRead.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> WorkspaceRead:
    """Update workspace details."""
    if workspace.id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify other workspace"
        )

    if payload.name:
        workspace.name = payload.name

    await session.commit()
    await session.refresh(workspace)

    return WorkspaceRead.model_validate(workspace)
