"""
Notification Dispatcher - Routes alerts to correct channel.

Handles deduplication, channel routing, and delivery status tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisCache, get_redis_client

if TYPE_CHECKING:
    from app.services.notifications.slack import SlackNotifier

logger = get_logger(__name__)


class NotificationDispatcher:
    """
    Routes alerts to configured notification channels.

    Features:
    - Alert deduplication (suppresses duplicates within dedup window)
    - Slack delivery with Block Kit formatting
    - Email delivery via Resend (Phase 2)
    - Delivery status tracking
    """

    def __init__(self, cache: RedisCache | None = None):
        self._cache: RedisCache | None = cache
        self.slack: SlackNotifier | None = None  # Lazy-loaded

    async def _get_cache(self) -> RedisCache:
        if self._cache is None:
            self._cache = RedisCache(await get_redis_client())
        return self._cache

    async def _get_slack_notifier(self) -> SlackNotifier:
        if self.slack is None:
            from app.services.notifications.slack import SlackNotifier

            self.slack = SlackNotifier()
        return self.slack

    async def send_alert(
        self,
        alert: dict,
        dedup_key: str | None = None,
    ) -> bool:
        """
        Send an alert to configured channels.

        Returns True if at least one channel delivered successfully.
        """
        if dedup_key:
            if await self._is_duplicate(dedup_key, alert.get("workspace_id")):
                logger.info("notification.deduped", key=dedup_key)
                return True

        success = False
        slack_notifier = await self._get_slack_notifier()

        if alert.get("workspace_id"):
            try:
                delivered = await slack_notifier.send_alert(
                    title=alert["title"],
                    description=alert["description"],
                    severity=alert["severity"],
                    suggested_fix=alert.get("suggested_fix"),
                )
                if delivered:
                    success = True
                    logger.info("notification.slack_delivered", alert_id=alert.get("id"))
            except Exception as e:
                logger.error("notification.slack_failed", alert_id=alert.get("id"), error=str(e))

        # Only suppress future duplicates once delivery actually succeeded, so a
        # transient failure does not silence this alert for the dedup window.
        if success and dedup_key:
            await self._mark_sent(dedup_key, alert.get("workspace_id"))

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
