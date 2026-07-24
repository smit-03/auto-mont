"""
Alert endpoints - Alert history queries.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.models.alert import Alert
from app.models.workspace import Workspace
from app.schemas.alert import AlertRead

router = APIRouter()


@router.get("", response_model=list[AlertRead])
async def list_alerts(
    severity: str | None = None,
    category: str | None = None,
    resolved: bool | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[AlertRead]:
    """List alerts for the current workspace, newest first."""
    query = select(Alert).where(Alert.workspace_id == workspace.id)

    if severity is not None:
        query = query.where(Alert.severity == severity)
    if category is not None:
        query = query.where(Alert.category == category)
    if resolved is True:
        query = query.where(Alert.resolved_at.is_not(None))
    elif resolved is False:
        query = query.where(Alert.resolved_at.is_(None))

    query = query.order_by(Alert.created_at.desc()).limit(min(limit, 100))

    result = await session.execute(query)
    alerts = result.scalars().all()
    return [AlertRead.model_validate(a) for a in alerts]


@router.get("/{alert_id}", response_model=AlertRead)
async def get_alert(
    alert_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> AlertRead:
    """Get a single alert scoped to the current workspace."""
    result = await session.execute(
        select(Alert).where(
            Alert.id == alert_id,
            Alert.workspace_id == workspace.id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return AlertRead.model_validate(alert)
