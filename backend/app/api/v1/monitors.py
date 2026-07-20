"""
Monitor endpoints - CRUD operations for heartbeat monitors.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.models.monitor import Monitor
from app.models.workspace import Workspace
from app.schemas.monitor import MonitorCreate, MonitorRead, MonitorStatus, MonitorUpdate
from app.services.heartbeat.engine import HeartbeatEngine

router = APIRouter()


async def _get_owned_monitor(
    monitor_id: uuid.UUID, workspace: Workspace, session: AsyncSession
) -> Monitor:
    """Fetch a monitor scoped to the workspace or raise 404."""
    result = await session.execute(
        select(Monitor).where(
            Monitor.id == monitor_id,
            Monitor.workspace_id == workspace.id,
            Monitor.deleted_at.is_(None),
        )
    )
    monitor = result.scalar_one_or_none()
    if not monitor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")
    return monitor


@router.get("", response_model=list[MonitorRead])
async def list_monitors(
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[MonitorRead]:
    """List all monitors for the current workspace."""
    result = await session.execute(
        select(Monitor).where(
            Monitor.workspace_id == workspace.id,
            Monitor.deleted_at.is_(None),
        )
    )
    monitors = result.scalars().all()
    return [MonitorRead.model_validate(m) for m in monitors]


@router.post("", response_model=MonitorRead, status_code=status.HTTP_201_CREATED)
async def create_monitor(
    payload: MonitorCreate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> MonitorRead:
    """Create a monitor. Heartbeat monitors get a unique ping URL."""
    ping_url = None
    if payload.monitor_type == "heartbeat":
        ping_url = f"/ping/{secrets.token_urlsafe(24)}"

    monitor = Monitor(
        workspace_id=workspace.id,
        integration_id=payload.integration_id,
        name=payload.name,
        description=payload.description,
        monitor_type=payload.monitor_type,
        ping_url=ping_url,
        expected_cron=payload.expected_cron,
        grace_period_s=payload.grace_period_s,
        expected_outcome=payload.expected_outcome,
    )
    session.add(monitor)
    await session.commit()
    await session.refresh(monitor)

    return MonitorRead.model_validate(monitor)


@router.get("/{monitor_id}", response_model=MonitorRead)
async def get_monitor(
    monitor_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> MonitorRead:
    """Get monitor details."""
    monitor = await _get_owned_monitor(monitor_id, workspace, session)
    return MonitorRead.model_validate(monitor)


@router.patch("/{monitor_id}", response_model=MonitorRead)
async def update_monitor(
    monitor_id: uuid.UUID,
    payload: MonitorUpdate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> MonitorRead:
    """Update monitor configuration."""
    monitor = await _get_owned_monitor(monitor_id, workspace, session)

    if payload.name is not None:
        monitor.name = payload.name
    if payload.description is not None:
        monitor.description = payload.description
    if payload.grace_period_s is not None:
        monitor.grace_period_s = payload.grace_period_s
    if payload.expected_cron is not None:
        monitor.expected_cron = payload.expected_cron
    if payload.expected_outcome is not None:
        monitor.expected_outcome = payload.expected_outcome
    if payload.alert_on_miss is not None:
        monitor.alert_on_miss = payload.alert_on_miss

    await session.commit()
    await session.refresh(monitor)

    return MonitorRead.model_validate(monitor)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(
    monitor_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> None:
    """Soft-delete a monitor."""
    monitor = await _get_owned_monitor(monitor_id, workspace, session)
    monitor.deleted_at = datetime.now(UTC)
    await session.commit()


@router.get("/{monitor_id}/status", response_model=MonitorStatus)
async def get_monitor_status(
    monitor_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> MonitorStatus:
    """Get live monitor status via the heartbeat engine."""
    monitor = await _get_owned_monitor(monitor_id, workspace, session)

    alert = await HeartbeatEngine().check_monitor(monitor)
    next_expected = None
    if monitor.last_ping_at:
        next_expected = monitor.last_ping_at + timedelta(seconds=monitor.grace_period_s)

    return MonitorStatus(
        status="failing" if alert else "healthy",
        last_ping_at=monitor.last_ping_at,
        next_expected=next_expected,
        missed_count=0,
    )
