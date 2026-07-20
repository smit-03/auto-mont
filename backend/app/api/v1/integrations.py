"""
Integration endpoints - CRUD operations for platform connections.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.core.security import get_credential_encryption
from app.models.integration import Integration
from app.models.workspace import Workspace
from app.schemas.integration import (
    IntegrationCreate,
    IntegrationRead,
    IntegrationTestResult,
    IntegrationUpdate,
)

router = APIRouter()


@router.get("", response_model=list[IntegrationRead])
async def list_integrations(
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[IntegrationRead]:
    """List all integrations for current workspace."""
    result = await session.execute(
        select(Integration).where(
            Integration.workspace_id == workspace.id,
            Integration.deleted_at.is_(None),
        )
    )
    integrations = result.scalars().all()
    return [IntegrationRead.model_validate(i) for i in integrations]


@router.post("", response_model=IntegrationRead, status_code=status.HTTP_201_CREATED)
async def create_integration(
    payload: IntegrationCreate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> IntegrationRead:
    """Connect a new platform integration."""
    encryption = get_credential_encryption()

    # Encrypt API key
    api_key_enc = encryption.encrypt(payload.api_key, str(workspace.id))
    api_key_hint = payload.api_key[-4:] if len(payload.api_key) >= 4 else ""

    integration = Integration(
        workspace_id=workspace.id,
        platform=payload.platform,
        display_name=payload.display_name,
        base_url=payload.base_url,
        api_key_enc=api_key_enc,
        api_key_hint=f"••••{api_key_hint}",
        poll_interval_s=payload.poll_interval_s,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)

    return IntegrationRead.model_validate(integration)


@router.get("/{integration_id}", response_model=IntegrationRead)
async def get_integration(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> IntegrationRead:
    """Get integration details."""
    result = await session.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == workspace.id,
            Integration.deleted_at.is_(None),
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")
    return IntegrationRead.model_validate(integration)


@router.patch("/{integration_id}", response_model=IntegrationRead)
async def update_integration(
    integration_id: uuid.UUID,
    payload: IntegrationUpdate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> IntegrationRead:
    """Update integration configuration."""
    result = await session.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == workspace.id,
            Integration.deleted_at.is_(None),
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")

    if payload.display_name:
        integration.display_name = payload.display_name
    if payload.polling_enabled is not None:
        integration.polling_enabled = payload.polling_enabled
    if payload.poll_interval_s:
        integration.poll_interval_s = payload.poll_interval_s

    await session.commit()
    await session.refresh(integration)

    return IntegrationRead.model_validate(integration)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> None:
    """Disconnect and archive an integration."""
    result = await session.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == workspace.id,
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")

    integration.deleted_at = datetime.utcnow()
    integration.status = "paused"
    await session.commit()


@router.post("/{integration_id}/test", response_model=IntegrationTestResult)
async def test_integration(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> IntegrationTestResult:
    """Test connectivity to the integration."""
    result = await session.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == workspace.id,
            Integration.deleted_at.is_(None),
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")

    # Use adapter to test connectivity
    from app.core.security import get_credential_encryption
    from app.services.sentinel.n8n_adapter import N8NAdapter

    encryption = get_credential_encryption()
    if integration.api_key_enc is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Integration has no stored API key to test",
        )
    api_key = encryption.decrypt(integration.api_key_enc, str(workspace.id))
    adapter = N8NAdapter(
        base_url=integration.base_url or "http://localhost:5678",
        api_key=api_key,
    )

    try:
        connected = await adapter.test_connectivity()
        return IntegrationTestResult(connected=connected)
    except Exception as e:
        return IntegrationTestResult(
            connected=False,
            error_message=str(e),
        )


@router.post("/{integration_id}/poll")
async def trigger_manual_poll(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> dict:
    """Trigger a manual poll for an integration."""
    from app.worker.tasks.polling import poll_integration_executions_async

    result = await session.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == workspace.id,
            Integration.deleted_at.is_(None),
        )
    )
    integration = result.scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    count = await poll_integration_executions_async(integration)
    return {"executions_fetched": count}
