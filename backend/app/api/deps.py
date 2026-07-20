"""
API Dependencies - FastAPI dependency injection for auth, db session.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import extract_workspace_id, verify_jwt_token
from app.database import AsyncSession, get_db_session
from app.models.workspace import Workspace


async def get_current_workspace(
    authorization: Annotated[str | None, Header()] = None,
) -> Workspace:
    """
    Get current workspace from Clerk JWT token.

    To be used as:
        workspace: Workspace = Depends(get_current_workspace)
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Authorization header")

    token = authorization[7:]  # Remove "Bearer " prefix
    payload = verify_jwt_token(token)

    if not payload:
        raise AuthenticationError("Invalid or expired token")

    workspace_id = extract_workspace_id(payload)
    if not workspace_id:
        raise AuthorizationError("No workspace found in token")

    # In production, fetch from DB
    return Workspace(id=workspace_id, name=f"Workspace {workspace_id[:8]}", slug=workspace_id)


async def get_workspace_id(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> str:
    """Extract workspace_id from session context or header."""
    # In production, this would extract from JWT token
    # For Phase 1 dev, we use a header override
    return "dev-workspace-id"


async def get_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> str | None:
    """Extract API key from header for non-authenticated endpoints."""
    if not x_api_key:
        return None
    if not x_api_key.startswith(("wrp_live_", "wrp_test_")):
        raise HTTPException(status_code=401, detail="Invalid API key format")
    return x_api_key
