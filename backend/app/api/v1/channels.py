"""
Notification channel endpoints — per-workspace alert destinations.

Implements the `/api/v1/channels` surface specified in Blueprint Part 6.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_workspace, get_db_session
from app.core.security import get_credential_encryption
from app.models.channel import NotificationChannel
from app.models.workspace import Workspace
from app.schemas.channel import (
    ChannelCreate,
    ChannelRead,
    ChannelTestResult,
    ChannelUpdate,
)
from app.services.notifications.email import EmailNotifier
from app.services.notifications.slack import SlackNotifier

router = APIRouter()


def _destination_hint(destination: str, channel_type: str) -> str:
    """
    Build a safe display fragment for a destination.

    Enough for a user to tell two channels apart, not enough to deliver to
    either. Webhooks show only a truncated trailing path segment; emails keep
    the domain and the first two characters of the local part, since a bare
    tail ("••••il.com") tells a user nothing about which address it is.
    """
    if channel_type == "email":
        local, _, domain = destination.partition("@")
        return f"{local[:2]}••••@{domain}"[:255] if domain else "••••"

    tail = destination.rstrip("/").rsplit("/", 1)[-1]
    return f"••••{tail[-6:]}" if len(tail) > 6 else "••••"


async def _get_owned_channel(
    channel_id: uuid.UUID, workspace: Workspace, session: AsyncSession
) -> NotificationChannel:
    """Fetch a channel scoped to the workspace or raise 404."""
    result = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.workspace_id == workspace.id,
            NotificationChannel.deleted_at.is_(None),
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


@router.get("", response_model=list[ChannelRead])
async def list_channels(
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[ChannelRead]:
    """List notification channels for the current workspace."""
    result = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.workspace_id == workspace.id,
            NotificationChannel.deleted_at.is_(None),
        )
    )
    return [ChannelRead.model_validate(c) for c in result.scalars().all()]


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def create_channel(
    payload: ChannelCreate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> ChannelRead:
    """Add a notification channel. The destination is encrypted at rest."""
    encryption = get_credential_encryption()

    channel = NotificationChannel(
        workspace_id=workspace.id,
        channel_type=payload.channel_type,
        display_name=payload.display_name,
        destination_enc=encryption.encrypt(payload.destination, str(workspace.id)),
        destination_hint=_destination_hint(payload.destination, payload.channel_type),
        min_severity=payload.min_severity,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)

    return ChannelRead.model_validate(channel)


@router.patch("/{channel_id}", response_model=ChannelRead)
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdate,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> ChannelRead:
    """Update a channel's display name, enabled flag, or severity threshold."""
    channel = await _get_owned_channel(channel_id, workspace, session)

    if payload.display_name is not None:
        channel.display_name = payload.display_name
    if payload.enabled is not None:
        channel.enabled = payload.enabled
    if payload.min_severity is not None:
        channel.min_severity = payload.min_severity

    await session.commit()
    await session.refresh(channel)

    return ChannelRead.model_validate(channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> None:
    """Soft-delete a notification channel."""
    channel = await _get_owned_channel(channel_id, workspace, session)
    channel.deleted_at = datetime.now(UTC)
    await session.commit()


@router.post("/{channel_id}/test", response_model=ChannelTestResult)
async def test_channel(
    channel_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> ChannelTestResult:
    """Send a test notification to verify the channel is wired up correctly."""
    channel = await _get_owned_channel(channel_id, workspace, session)

    encryption = get_credential_encryption()
    try:
        destination = encryption.decrypt(channel.destination_enc, str(workspace.id))
    except Exception:
        return ChannelTestResult(
            delivered=False,
            error_message="Stored destination could not be decrypted",
        )

    # Dispatch by type. This read the destination as a Slack webhook whatever
    # the channel was, so testing an email channel posted the subscriber's
    # address at Slack and reported a nonsense failure.
    if channel.channel_type == "email":
        delivered = await EmailNotifier().send_test(recipient=destination)
        rejection = "Email provider rejected the test message"
    else:
        delivered = await SlackNotifier(webhook_url=destination).send_test()
        rejection = "Slack rejected the test message"

    return ChannelTestResult(
        delivered=delivered,
        error_message=None if delivered else rejection,
    )
