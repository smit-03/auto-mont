"""
Unit tests for Resend email delivery — Phase 2 deliverable 7.

Two things matter here and nothing else does. First, that alert text reaching an
HTML document is escaped: titles and descriptions carry workflow names and raw
platform error messages, which are accident- and attacker-influenced. Second,
that a workspace with both Slack and email configured gets both — one
transport's outage must not suppress the other, since the customer asked for
both destinations explicitly.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.services.notifications.dispatcher import NotificationDispatcher
from app.services.notifications.email import EmailNotifier


class InMemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self.store[key] = value
        return True


def ok_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    return response


def notifier() -> EmailNotifier:
    return EmailNotifier(api_key="re_test_key", from_email="WRP <alerts@test.dev>")


async def send(notifier_: EmailNotifier, **kwargs):
    """Send an alert with a mocked transport; returns (result, posted_json)."""
    defaults = {
        "title": "Auth failure: Order Sync",
        "description": "OAuth refresh token expired",
        "severity": "critical",
        "recipient": "ops@test.dev",
    }
    defaults.update(kwargs)

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok_response())) as post:
        result = await notifier_.send_alert(**defaults)

    return result, (post.await_args.kwargs["json"] if post.await_args else None)


class TestPayload:
    @pytest.mark.asyncio
    async def test_sends_to_the_channels_recipient(self):
        result, payload = await send(notifier(), recipient="oncall@test.dev")

        assert result is True
        assert payload["to"] == ["oncall@test.dev"]
        assert payload["from"] == "WRP <alerts@test.dev>"

    @pytest.mark.asyncio
    async def test_subject_leads_with_severity(self):
        """
        The subject is all that survives a mailbox list view, where the body is
        invisible and the subject alone decides whether it gets opened.
        """
        _, payload = await send(notifier(), severity="critical", title="Order Sync down")

        assert payload["subject"] == "[CRITICAL] Order Sync down"

    @pytest.mark.asyncio
    async def test_api_key_is_sent_as_a_bearer_token(self):
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok_response())) as post:
            await notifier().send_alert(
                title="t", description="d", severity="info", recipient="a@test.dev"
            )

        assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer re_test_key"

    @pytest.mark.asyncio
    async def test_suggested_fix_is_included_when_present(self):
        _, payload = await send(notifier(), suggested_fix="Re-authenticate the credential")

        assert "Re-authenticate the credential" in payload["html"]

    @pytest.mark.asyncio
    async def test_omits_the_fix_block_entirely_when_absent(self):
        _, payload = await send(notifier(), suggested_fix=None)

        assert "Suggested fix" not in payload["html"]


class TestEscaping:
    """
    Alert text is not authored by us. Workflow names come from the customer and
    error messages come from whatever the failing API returned.
    """

    @pytest.mark.asyncio
    async def test_title_is_escaped(self):
        _, payload = await send(notifier(), title="<script>alert(1)</script>")

        assert "<script>" not in payload["html"]
        assert "&lt;script&gt;" in payload["html"]

    @pytest.mark.asyncio
    async def test_description_is_escaped(self):
        _, payload = await send(notifier(), description='" onload="steal()')

        assert 'onload="steal()' not in payload["html"]

    @pytest.mark.asyncio
    async def test_suggested_fix_is_escaped(self):
        _, payload = await send(notifier(), suggested_fix="<img src=x onerror=1>")

        assert "<img" not in payload["html"]


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_missing_api_key_fails_without_calling_out(self):
        """
        An unconfigured Resend key must not produce a confusing HTTP error.

        The key is cleared after construction rather than passed as None: the
        constructor falls back to settings.RESEND_API_KEY, which is populated in
        a developer's .env, so `api_key=None` does not simulate an unconfigured
        environment.
        """
        unconfigured = EmailNotifier(api_key="placeholder")
        unconfigured.api_key = None

        with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
            result = await unconfigured.send_alert(
                title="t", description="d", severity="info", recipient="a@test.dev"
            )

        assert result is False
        assert post.await_count == 0

    @pytest.mark.asyncio
    async def test_missing_recipient_fails_without_calling_out(self):
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
            result = await notifier().send_alert(
                title="t", description="d", severity="info", recipient=None
            )

        assert result is False
        assert post.await_count == 0

    @pytest.mark.asyncio
    async def test_rejection_by_resend_raises_for_the_dispatcher_to_record(self):
        """Mirrors SlackNotifier: an upstream rejection names the failing channel."""
        response = MagicMock()
        response.status_code = 422
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("bad", request=MagicMock(), response=response)
        )

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
            with pytest.raises(ExternalServiceError):
                await notifier().send_alert(
                    title="t", description="d", severity="info", recipient="a@test.dev"
                )


class TestDispatcherRouting:
    """The dispatcher picks a transport per channel row."""

    @pytest.mark.asyncio
    async def test_email_channel_routes_to_the_email_notifier(self):
        dispatcher = NotificationDispatcher(
            cache=InMemoryCache(),
            channels=[("email", "ops-inbox", "ops@test.dev")],
        )
        dispatcher.email = EmailNotifier(api_key="re_test_key")
        dispatcher.slack = MagicMock()

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok_response())):
            delivered = await dispatcher.send_alert(
                {
                    "workspace_id": "ws-1",
                    "title": "t",
                    "description": "d",
                    "severity": "critical",
                }
            )

        assert delivered is True
        # The Slack notifier must not be involved in an email-only workspace.
        assert not dispatcher.slack.send_alert.called

    @pytest.mark.asyncio
    async def test_slack_failure_does_not_suppress_the_email(self):
        """
        A workspace configuring both asked for both. Delivering only to whichever
        transport happens to be healthy first would make the second destination
        quietly unreliable.
        """
        dispatcher = NotificationDispatcher(
            cache=InMemoryCache(),
            channels=[
                ("slack", "ops-slack", "https://hooks.slack.test/x"),
                ("email", "ops-inbox", "ops@test.dev"),
            ],
        )
        dispatcher.slack = MagicMock()
        dispatcher.slack.send_alert = AsyncMock(side_effect=RuntimeError("slack down"))
        dispatcher.email = EmailNotifier(api_key="re_test_key")

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok_response())) as post:
            delivered = await dispatcher.send_alert(
                {
                    "workspace_id": "ws-1",
                    "title": "t",
                    "description": "d",
                    "severity": "critical",
                }
            )

        assert delivered is True
        assert post.await_count == 1
