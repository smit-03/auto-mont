"""
Workspace endpoints.

There is deliberately no `POST /workspaces`. Workspaces are provisioned lazily
by `get_current_workspace()` on a user's first authenticated request, keyed by
their Clerk identity, so a create endpoint is redundant — and the previous one
required no authentication at all, letting any unauthenticated caller insert
arbitrary workspace rows. Renaming is handled by the authenticated
`PATCH /workspaces/{id}` below.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.schemas.workspace import WorkspaceRead, WorkspaceUpdate

router = APIRouter()


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
