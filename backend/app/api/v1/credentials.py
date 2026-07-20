"""
Credential endpoints - OAuth credential vault CRUD operations.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.models.credential import Credential
from app.models.workspace import Workspace
from app.schemas.credential import CredentialCreate, CredentialRead
from app.services.credential_vault.vault import (
    EXPIRY_WARNING_HOURS,
    get_vault,
)

router = APIRouter()


async def _get_owned_credential(
    credential_id: uuid.UUID, workspace: Workspace, session: AsyncSession
) -> Credential:
    """Fetch a credential scoped to the workspace or raise 404."""
    result = await session.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.workspace_id == workspace.id,
            Credential.deleted_at.is_(None),
        )
    )
    credential = result.scalar_one_or_none()
    if not credential:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return credential


def _to_read(credential: Credential) -> CredentialRead:
    """Build a CredentialRead with computed expiry fields."""
    read = CredentialRead.model_validate(credential)
    if credential.expires_at:
        hours = (credential.expires_at - datetime.now(UTC)).total_seconds() / 3600
        read.hours_until_expiry = hours
        read.is_expiring_soon = hours <= EXPIRY_WARNING_HOURS
    return read


@router.get("", response_model=list[CredentialRead])
async def list_credentials(
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[CredentialRead]:
    """List all tracked credentials for the current workspace."""
    result = await session.execute(
        select(Credential).where(
            Credential.workspace_id == workspace.id,
            Credential.deleted_at.is_(None),
        )
    )
    credentials = result.scalars().all()
    return [_to_read(c) for c in credentials]


@router.post("", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
async def create_credential(
    payload: CredentialCreate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> CredentialRead:
    """Register a credential for expiry tracking (tokens encrypted at rest)."""
    vault = get_vault()
    token_enc, refresh_enc = await vault.encrypt_credential(
        payload.token, payload.refresh_token, str(workspace.id)
    )

    credential = Credential(
        workspace_id=workspace.id,
        integration_id=payload.integration_id,
        provider=payload.provider,
        display_name=payload.display_name,
        token_enc=token_enc,
        refresh_enc=refresh_enc,
        expires_at=payload.expires_at,
        scopes=payload.scopes,
        gcp_status=payload.gcp_status,
    )
    session.add(credential)
    await session.commit()
    await session.refresh(credential)

    return _to_read(credential)


@router.get("/{credential_id}", response_model=CredentialRead)
async def get_credential(
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> CredentialRead:
    """Get credential status detail."""
    credential = await _get_owned_credential(credential_id, workspace, session)
    return _to_read(credential)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> None:
    """Soft-delete a tracked credential."""
    credential = await _get_owned_credential(credential_id, workspace, session)
    credential.deleted_at = datetime.now(UTC)
    await session.commit()
