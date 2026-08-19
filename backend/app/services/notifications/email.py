"""
Email alert delivery via Resend — Phase 2 deliverable 7.

Mirrors SlackNotifier's contract deliberately: same method name, same return
semantics (True on delivery, False on a failure the dispatcher should log and
move past), so the dispatcher routes by channel type without special-casing.

The recipient address is the channel's decrypted destination, exactly as the
Slack webhook URL is. It is passed in per call rather than held on the notifier,
because one notifier serves every workspace.
"""

import html

import httpx

from app.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

SEVERITY_COLORS: dict[str, str] = {
    "critical": "#b91c1c",
    "warning": "#b45309",
    "info": "#1d4ed8",
}


class EmailNotifier:
    """Sends alert emails through Resend's HTTP API."""

    def __init__(self, api_key: str | None = None, from_email: str | None = None):
        self.api_key = api_key or settings.RESEND_API_KEY
        self.from_email = from_email or settings.RESEND_FROM_EMAIL

    def _build_html(
        self,
        title: str,
        description: str,
        severity: str,
        suggested_fix: str | None,
    ) -> str:
        """
        Render the alert body.

        Every interpolated value is escaped: titles and descriptions carry
        workflow names and raw platform error messages, which are attacker- and
        accident-influenced text landing in an HTML document.
        """
        color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"])
        fix_block = ""
        if suggested_fix:
            fix_block = (
                '<p style="margin:16px 0 4px;font-size:12px;text-transform:uppercase;'
                'letter-spacing:.05em;color:#6b7280;">Suggested fix</p>'
                f'<p style="margin:0;color:#374151;">{html.escape(suggested_fix)}</p>'
            )

        return (
            '<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;">'
            f'<div style="border-left:4px solid {color};padding:12px 16px;background:#f9fafb;">'
            f'<p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;'
            f'letter-spacing:.05em;color:{color};">{html.escape(severity)}</p>'
            f'<h2 style="margin:0;font-size:18px;color:#111827;">{html.escape(title)}</h2>'
            "</div>"
            f'<p style="margin:16px 0;color:#374151;">{html.escape(description)}</p>'
            f"{fix_block}"
            '<p style="margin-top:24px;font-size:12px;color:#9ca3af;">'
            "Workflow Reliability Platform</p>"
            "</div>"
        )

    async def send_alert(
        self,
        title: str,
        description: str,
        severity: str,
        suggested_fix: str | None = None,
        recipient: str | None = None,
        **kwargs: object,
    ) -> bool:
        """Send one alert email. Returns True when Resend accepts it."""
        if not self.api_key:
            logger.error("email.no_api_key", title=title)
            return False
        if not recipient:
            logger.error("email.no_recipient", title=title)
            return False

        payload = {
            "from": self.from_email,
            "to": [recipient],
            # The severity prefix survives mailbox list views, where the body
            # is invisible and only the subject decides whether it gets opened.
            "subject": f"[{severity.upper()}] {title}",
            "html": self._build_html(title, description, severity, suggested_fix),
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info("email.alert_sent", title=title, severity=severity)
                return True
        except httpx.HTTPStatusError as e:
            # Matches SlackNotifier: an upstream rejection is raised so the
            # dispatcher records which channel failed, rather than being
            # flattened into a bare False.
            logger.error("email.alert_failed", status=e.response.status_code, error=str(e))
            raise ExternalServiceError(
                message=f"Email delivery failed: {e.response.status_code}",
                service="resend",
            ) from e
        except Exception as e:
            logger.error("email.alert_error", error=str(e))
            return False

    async def send_test(self, recipient: str | None = None) -> bool:
        """
        Send a test email to verify a channel is wired up.

        Mirrors SlackNotifier.send_test, which never raises: the /test endpoint
        reports a failed delivery as a result, not as a 500. send_alert raises
        on an upstream rejection, so that is caught and folded into False here.
        """
        if not self.api_key or not recipient:
            return False

        try:
            return await self.send_alert(
                title="WRP email integration test",
                description=(
                    "This is a test message. Your email alert channel is "
                    "configured correctly."
                ),
                severity="info",
                recipient=recipient,
            )
        except ExternalServiceError:
            return False
