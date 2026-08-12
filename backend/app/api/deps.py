"""
API Dependencies - FastAPI dependency injection for auth, db session.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import extract_workspace_id, verify_jwt_token
from app.database import get_db_session
from app.models.workspace import Workspace


async def _get_workspace_by_clerk_id(session: AsyncSession, clerk_id: str) -> Workspace | None:
    result = await session.execute(
        select(Workspace).where(
            Workspace.clerk_id == clerk_id,
            Workspace.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_current_workspace(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    """
    Resolve the current workspace from the Clerk JWT, provisioning on first login.

    The Clerk identity used as the workspace key is the organization id
    (`org_id`) when the user is acting within a Clerk Organization, otherwise
    the user's `sub`. This precedence is fixed and intentional: a personal
    login and an organization login are distinct identities and map to
    distinct workspaces. Merging a user's personal and org contexts is out of
    scope for Phase 1.

    The Clerk id is stored in `workspaces.clerk_id` (unique), NOT the UUID PK.
    On first login for a given clerk_id, a workspace row is auto-provisioned.
    Provisioning is race-safe: concurrent first-login requests that lose the
    unique-constraint insert fall back to re-reading the winner's row.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Authorization header")

    token = authorization[7:]  # Remove "Bearer " prefix
    payload = verify_jwt_token(token)

    if not payload:
        raise AuthenticationError("Invalid or expired token")

    clerk_id = extract_workspace_id(payload)
    if not clerk_id:
        raise AuthorizationError("No workspace identity found in token")

    workspace = await _get_workspace_by_clerk_id(session, clerk_id)
    if workspace is not None:
        return workspace

    # First login for this Clerk identity: provision a workspace with a real
    # UUID PK. Slug is derived from the new PK to guarantee uniqueness.
    # FUTURE: derive a human-readable slug from the user's email/name before
    # this is customer-facing.
    workspace_id = uuid.uuid4()
    workspace = Workspace(
        id=workspace_id,
        clerk_id=clerk_id,
        name=f"Workspace {str(workspace_id)[:8]}",
        slug=f"ws-{str(workspace_id)[:8]}",
    )
    session.add(workspace)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request for the same clerk_id won the insert race.
        # Roll back and read back the workspace it created.
        await session.rollback()
        workspace = await _get_workspace_by_clerk_id(session, clerk_id)
        if workspace is None:
            raise
        return workspace

    await session.refresh(workspace)
    return workspace


async def get_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> str | None:
    """Extract API key from header for non-authenticated endpoints."""
    if not x_api_key:
        return None
    if not x_api_key.startswith(("wrp_live_", "wrp_test_")):
        raise HTTPException(status_code=401, detail="Invalid API key format")
    return x_api_key
