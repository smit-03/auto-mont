"""
Notification Dispatcher - Routes alerts to correct channel.

Handles deduplication, channel routing, and delivery status tracking.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisCache, get_redis_client

if TYPE_CHECKING:
    from app.services.notifications.email import EmailNotifier
    from app.services.notifications.slack import SlackNotifier

logger = get_logger(__name__)

# Channel types this dispatcher knows how to deliver to. A row of any other
# type is skipped rather than mis-delivered — `pagerduty` is a valid value in
# the model but has no transport yet.
DELIVERABLE_CHANNEL_TYPES = ("slack", "email")


class NotificationDispatcher:
    """
    Routes alerts to configured notification channels.

    Features:
    - Alert deduplication (suppresses duplicates within dedup window)
    - Slack delivery with Block Kit formatting
    - Email delivery via Resend (Phase 2)
    - Delivery status tracking
    """

    def __init__(
        self,
        cache: RedisCache | None = None,
        channels: list[tuple[str, str, str]] | None = None,
    ):
        self._cache: RedisCache | None = cache
        self.slack: SlackNotifier | None = None  # Lazy-loaded
        self.email: EmailNotifier | None = None  # Lazy-loaded
        # Explicit (channel_type, display_name, destination) triples, bypassing
        # lookup. Test seam only — production always resolves per workspace.
        self._channels_override = channels

    async def _get_cache(self) -> RedisCache:
        if self._cache is None:
            self._cache = RedisCache(await get_redis_client())
        return self._cache

    async def _get_slack_notifier(self) -> SlackNotifier:
        if self.slack is None:
            from app.services.notifications.slack import SlackNotifier

            self.slack = SlackNotifier()
        return self.slack

    async def _get_email_notifier(self) -> EmailNotifier:
        if self.email is None:
            from app.services.notifications.email import EmailNotifier

            self.email = EmailNotifier()
        return self.email

    async def _resolve_channels(
        self, workspace_id: str, severity: str
    ) -> list[tuple[str, str, str]]:
        """
        Return (channel_type, display_name, destination) for this workspace.

        Destinations are decrypted here and never cached — they are credentials.
        A channel whose destination fails to decrypt is skipped and logged
        rather than aborting delivery to the workspace's other channels.
        """
        if self._channels_override is not None:
            return self._channels_override

        from sqlalchemy import select

        from app.core.security import get_credential_encryption
        from app.database import get_session_factory
        from app.models.channel import SEVERITY_ORDER, NotificationChannel

        threshold = SEVERITY_ORDER.get(severity, 0)

        async with get_session_factory().begin() as session:
            result = await session.execute(
                select(NotificationChannel).where(
                    NotificationChannel.workspace_id == uuid.UUID(workspace_id),
                    NotificationChannel.deleted_at.is_(None),
                    NotificationChannel.enabled.is_(True),
                    NotificationChannel.channel_type.in_(DELIVERABLE_CHANNEL_TYPES),
                )
            )
            rows = list(result.scalars().all())

        encryption = get_credential_encryption()
        resolved: list[tuple[str, str, str]] = []
        for channel in rows:
            if SEVERITY_ORDER.get(channel.min_severity, 0) > threshold:
                continue
            try:
                destination = encryption.decrypt(channel.destination_enc, str(channel.workspace_id))
            except Exception as e:
                logger.error(
                    "notification.channel_decrypt_failed",
                    channel_id=str(channel.id),
                    error=str(e),
                )
                continue
            resolved.append((channel.channel_type, channel.display_name, destination))

        return resolved

    async def send_alert(
        self,
        alert: dict,
        dedup_key: str | None = None,
    ) -> bool:
        """
        Send an alert to the alert's own workspace's channels.

        Returns True if at least one channel delivered successfully.
        """
        workspace_id = alert.get("workspace_id")
        if not workspace_id:
            logger.error("notification.no_workspace", alert_id=alert.get("id"))
            return False

        if dedup_key:
            if await self._is_duplicate(dedup_key, workspace_id):
                logger.info("notification.deduped", key=dedup_key)
                return True

        severity = alert.get("severity", "warning")
        channels = await self._resolve_channels(workspace_id, severity)

        if not channels:
            # No cross-workspace fallback in production. Falling back to a
            # global webhook is exactly the cross-tenant leak this replaced:
            # one workspace's workflow names and error messages would be
            # delivered into a channel another customer can read.
            if settings.ENVIRONMENT in ("development", "test") and settings.SLACK_WEBHOOK_URL:
                logger.warning(
                    "notification.dev_fallback_channel",
                    workspace_id=workspace_id,
                    detail="No channel configured; using SLACK_WEBHOOK_URL (non-production only)",
                )
                channels = [("slack", "dev-fallback", settings.SLACK_WEBHOOK_URL)]
            else:
                logger.error(
                    "notification.no_channels_configured",
                    workspace_id=workspace_id,
                    alert_id=alert.get("id"),
                    detail="Alert recorded but undeliverable — workspace has no channel",
                )
                return False

        success = False

        # One failing channel must not stop the others: a workspace with Slack
        # and email configured has asked for both, and a Slack outage is not a
        # reason to withhold the email.
        for channel_type, display_name, destination in channels:
            try:
                if channel_type == "email":
                    notifier = await self._get_email_notifier()
                    delivered = await notifier.send_alert(
                        title=alert["title"],
                        description=alert["description"],
                        severity=alert["severity"],
                        suggested_fix=alert.get("suggested_fix"),
                        recipient=destination,
                    )
                else:
                    slack_notifier = await self._get_slack_notifier()
                    delivered = await slack_notifier.send_alert(
                        title=alert["title"],
                        description=alert["description"],
                        severity=alert["severity"],
                        suggested_fix=alert.get("suggested_fix"),
                        webhook_url=destination,
                    )
                if delivered:
                    success = True
                    logger.info(
                        "notification.delivered",
                        alert_id=alert.get("id"),
                        channel=display_name,
                        channel_type=channel_type,
                    )
            except Exception as e:
                logger.error(
                    "notification.delivery_failed",
                    alert_id=alert.get("id"),
                    channel=display_name,
                    channel_type=channel_type,
                    error=str(e),
                )

        # Only suppress future duplicates once delivery actually succeeded, so a
        # transient failure does not silence this alert for the dedup window.
        if success and dedup_key:
            await self._mark_sent(dedup_key, workspace_id)

        return success

    async def _is_duplicate(self, key: str, workspace_id: str | None) -> bool:
        """Return True if an alert with this dedup key was recently delivered."""
        cache = await self._get_cache()
        dedup_key = f"dedup:alert:{workspace_id}:{key}"
        return await cache.exists(dedup_key)

    async def _mark_sent(self, key: str, workspace_id: str | None) -> None:
        """Record a successful delivery to suppress duplicates within the window."""
        cache = await self._get_cache()
        dedup_key = f"dedup:alert:{workspace_id}:{key}"
        await cache.set(dedup_key, "1", ttl=settings.ALERT_DEDUP_WINDOW_SECONDS)

    @staticmethod
    def build_dedup_key(
        integration_id: str | None,
        category: str,
        root_cause: str,
    ) -> str:
        """Build deduplication key from alert attributes."""
        parts = [str(integration_id), category, root_cause]
        return ":".join(p for p in parts if p)


# Global dispatcher instance
_dispatcher: NotificationDispatcher | None = None


def get_dispatcher() -> NotificationDispatcher:
    """Get the global notification dispatcher instance."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotificationDispatcher()
    return _dispatcher
