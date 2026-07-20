"""
Slack notification service - Block Kit format delivery.
"""

import httpx

from app.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)


class SlackNotifier:
    """
    Slack notification service with Block Kit formatting.
    """

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or settings.SLACK_WEBHOOK_URL

    async def send_alert(
        self,
        title: str,
        description: str,
        severity: str,
        suggested_fix: str | None = None,
        **kwargs: object,
    ) -> bool:
        """Send an alert to Slack via incoming webhook."""
        if not self.webhook_url:
            logger.warning("slack.webhook_not_configured")
            return False

        blocks = self._build_alert_blocks(
            title=title,
            description=description,
            severity=severity,
            suggested_fix=suggested_fix,
            **kwargs,
        )

        payload = {
            "text": f"[{severity.upper()}] {title}",
            "blocks": blocks,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info("slack.alert_sent", title=title, severity=severity)
                return True
        except httpx.HTTPStatusError as e:
            logger.error("slack.alert_failed", status=e.response.status_code, error=str(e))
            raise ExternalServiceError(
                message=f"Slack delivery failed: {e.response.status_code}",
                service="slack",
            ) from e
        except Exception as e:
            logger.error("slack.alert_error", error=str(e))
            return False

    def _build_alert_blocks(
        self,
        title: str,
        description: str,
        severity: str,
        suggested_fix: str | None = None,
        **kwargs: object,
    ) -> list[dict]:
        """Build Slack Block Kit blocks for an alert."""

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"[{severity.upper()}] {title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": description,
                },
            },
        ]

        if suggested_fix:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Suggested fix:*\n{suggested_fix}",
                    },
                }
            )

        # Add action buttons for HITL (Phase 2 feature, placeholders for now)
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "View in Dashboard",
                            "emoji": True,
                        },
                        "url": settings.CORS_ORIGINS[0]
                        if settings.CORS_ORIGINS
                        else "http://localhost:3000",
                    }
                ],
            }
        )

        return blocks

    async def send_test(self, message: str = "WRP Slack integration test") -> bool:
        """Send a test notification."""
        if not self.webhook_url:
            return False

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json={"text": message},
                    timeout=10.0,
                )
                response.raise_for_status()
                return True
        except Exception:
            return False
