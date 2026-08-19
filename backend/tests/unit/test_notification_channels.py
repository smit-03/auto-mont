"""
Tests for per-workspace alert delivery.

These cover the cross-tenant leak directly: alerts must reach only the channels
belonging to the alert's own workspace, and a workspace with no channel must
produce no delivery at all in production rather than falling back to a shared
destination.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.v1.channels import _destination_hint
from app.core.security import CredentialEncryption
from app.models.channel import NotificationChannel
from app.schemas.channel import ChannelCreate
from app.services.notifications.dispatcher import NotificationDispatcher
from app.services.notifications.slack import SlackNotifier

ENCRYPTION = CredentialEncryption(bytes.fromhex("ab" * 32))


class InMemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self.store[key] = value
        return True


def _channel(workspace_id, name, url, min_severity="warning", channel_type="slack"):
    """Build an unpersisted channel with a correctly encrypted destination."""
    return NotificationChannel(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        channel_type=channel_type,
        display_name=name,
        destination_enc=ENCRYPTION.encrypt(url, str(workspace_id)),
        destination_hint="••••test",
        enabled=True,
        min_severity=min_severity,
    )


class _FakeFactory:
    """Session factory whose SELECT returns `rows` and records the statement."""

    def __init__(self, rows):
        self.rows = rows
        self.statements: list = []

    def begin(self):
        outer = self

        class _Session:
            async def execute(self, stmt):
                outer.statements.append(stmt)
                result = MagicMock()
                scalars = MagicMock()
                scalars.all = MagicMock(return_value=outer.rows)
                result.scalars = MagicMock(return_value=scalars)
                return result

        class _Ctx:
            async def __aenter__(self_inner):
                return _Session()

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


def _patch_resolution(rows):
    """Patch the collaborators _resolve_channels imports at call time."""
    factory = _FakeFactory(rows)
    return factory, (
        patch("app.database.get_session_factory", return_value=factory),
        patch("app.core.security.get_credential_encryption", return_value=ENCRYPTION),
    )


class TestChannelResolution:
    @pytest.mark.asyncio
    async def test_query_is_scoped_to_the_requesting_workspace(self):
        """The channel lookup must filter on the alert's own workspace_id."""
        ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
        factory, (p_fac, p_enc) = _patch_resolution([_channel(ws_a, "a", "https://a.test")])

        with p_fac, p_enc:
            resolved = await NotificationDispatcher()._resolve_channels(str(ws_a), "critical")

        assert resolved == [("slack", "a", "https://a.test")]

        # The isolation property: workspace_id is bound into the WHERE clause,
        # and it is the requesting workspace, not any other.
        params = factory.statements[0].compile().params
        bound = {v for v in params.values() if isinstance(v, uuid.UUID)}
        assert ws_a in bound
        assert ws_b not in bound

    @pytest.mark.asyncio
    async def test_channel_below_min_severity_is_skipped(self):
        """A critical-only channel must not receive a warning."""
        ws = uuid.uuid4()
        rows = [
            _channel(ws, "all", "https://all.test", min_severity="info"),
            _channel(ws, "crit-only", "https://crit.test", min_severity="critical"),
        ]
        _, (p_fac, p_enc) = _patch_resolution(rows)

        with p_fac, p_enc:
            warning = await NotificationDispatcher()._resolve_channels(str(ws), "warning")
            critical = await NotificationDispatcher()._resolve_channels(str(ws), "critical")

        assert [n for _, n, _ in warning] == ["all"]
        assert [n for _, n, _ in critical] == ["all", "crit-only"]

    @pytest.mark.asyncio
    async def test_undecryptable_channel_is_skipped_not_fatal(self):
        """One corrupt destination must not block the workspace's other channels."""
        ws = uuid.uuid4()
        good = _channel(ws, "good", "https://good.test")
        broken = _channel(ws, "broken", "https://broken.test")
        broken.destination_enc = b"not-valid-ciphertext"
        _, (p_fac, p_enc) = _patch_resolution([broken, good])

        with p_fac, p_enc:
            resolved = await NotificationDispatcher()._resolve_channels(str(ws), "critical")

        assert resolved == [("slack", "good", "https://good.test")]


class TestDelivery:
    ALERT = {
        "id": "alert_1",
        "workspace_id": str(uuid.uuid4()),
        "title": "auth failure",
        "description": "token expired",
        "severity": "critical",
        "suggested_fix": "Re-authenticate.",
    }

    @pytest.mark.asyncio
    async def test_alert_fans_out_to_every_channel_of_its_workspace(self):
        dispatcher = NotificationDispatcher(
            cache=InMemoryCache(),
            channels=[
                ("slack", "ops", "https://ops.test"),
                ("slack", "oncall", "https://oncall.test"),
            ],
        )
        dispatcher.slack = SlackNotifier()

        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok)) as post:
            delivered = await dispatcher.send_alert(alert=self.ALERT, dedup_key="k")

        assert delivered is True
        assert post.await_count == 2
        posted = {c.args[0] if c.args else c.kwargs["url"] for c in post.await_args_list}
        assert posted == {"https://ops.test", "https://oncall.test"}

    @pytest.mark.asyncio
    async def test_no_channels_in_production_delivers_nothing(self, monkeypatch):
        """The leak-prevention property: no channel means no send, ever."""
        import app.services.notifications.dispatcher as disp

        monkeypatch.setattr(disp.settings, "ENVIRONMENT", "production", raising=False)
        monkeypatch.setattr(
            disp.settings, "SLACK_WEBHOOK_URL", "https://global.test", raising=False
        )

        dispatcher = NotificationDispatcher(cache=InMemoryCache(), channels=[])
        dispatcher.slack = SlackNotifier()

        with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
            delivered = await dispatcher.send_alert(alert=self.ALERT, dedup_key="k")

        assert delivered is False
        post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_channels_in_development_uses_local_fallback(self, monkeypatch):
        """Local development still works without configuring a channel."""
        import app.services.notifications.dispatcher as disp

        monkeypatch.setattr(disp.settings, "ENVIRONMENT", "development", raising=False)
        monkeypatch.setattr(disp.settings, "SLACK_WEBHOOK_URL", "https://dev.test", raising=False)

        dispatcher = NotificationDispatcher(cache=InMemoryCache(), channels=[])
        dispatcher.slack = SlackNotifier()

        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok)) as post:
            delivered = await dispatcher.send_alert(alert=self.ALERT, dedup_key="k")

        assert delivered is True
        post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_without_workspace_is_not_delivered(self):
        """An alert with no tenant has no correct destination."""
        dispatcher = NotificationDispatcher(
            cache=InMemoryCache(), channels=[("slack", "ops", "https://ops.test")]
        )
        dispatcher.slack = SlackNotifier()

        with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
            delivered = await dispatcher.send_alert(alert={**self.ALERT, "workspace_id": None})

        assert delivered is False
        post.assert_not_awaited()


class TestChannelCreateValidation:
    """
    A destination that does not match its channel type is rejected at the door.

    Nothing downstream re-checks it: the dispatcher posts a slack destination to
    httpx and hands an email destination to Resend. A swapped pair therefore
    surfaces as a silently undelivered alert, which is the one failure mode this
    product cannot afford.
    """

    WEBHOOK = "https://hooks.slack.com/services/T000/B000/xxxxxxxx"

    def test_accepts_a_slack_webhook(self):
        channel = ChannelCreate(display_name="ops", destination=self.WEBHOOK)
        assert channel.channel_type == "slack"

    def test_accepts_an_email_address(self):
        channel = ChannelCreate(
            channel_type="email", display_name="oncall", destination="ops@example.com"
        )
        assert channel.destination == "ops@example.com"

    def test_rejects_an_email_given_as_a_slack_destination(self):
        with pytest.raises(ValidationError, match="https"):
            ChannelCreate(display_name="ops", destination="ops@example.com")

    def test_rejects_a_webhook_given_as_an_email_destination(self):
        with pytest.raises(ValidationError, match="valid email"):
            ChannelCreate(channel_type="email", display_name="ops", destination=self.WEBHOOK)

    def test_rejects_a_plaintext_http_webhook(self):
        """A webhook carries alert content; http would put it on the wire."""
        with pytest.raises(ValidationError, match="https"):
            ChannelCreate(
                display_name="ops", destination="http://hooks.slack.com/services/T/B/x"
            )

    def test_rejects_a_webhook_on_an_unrelated_host(self):
        with pytest.raises(ValidationError, match="slack.com"):
            ChannelCreate(display_name="ops", destination="https://evil.test/services/T/B/x")

    def test_accepts_an_enterprise_grid_subdomain(self):
        channel = ChannelCreate(
            display_name="ops", destination="https://acme.slack.com/services/T/B/xxxxxx"
        )
        assert channel.channel_type == "slack"

    def test_rejects_a_host_merely_ending_in_the_brand(self):
        """"notslack.com" must not pass as a slack.com subdomain."""
        with pytest.raises(ValidationError, match="slack.com"):
            ChannelCreate(display_name="ops", destination="https://notslack.com/services/T/B/x")


class TestDestinationHint:
    """The hint is shown in the UI, so it must identify without disclosing."""

    def test_email_hint_keeps_the_domain_and_masks_the_local_part(self):
        hint = _destination_hint("smitpativala03@gmail.com", "email")
        assert hint == "sm••••@gmail.com"
        assert "pativala03" not in hint

    def test_webhook_hint_does_not_expose_the_secret_path(self):
        secret = "xoxbSECRETTOKEN"
        hint = _destination_hint(f"https://hooks.slack.com/services/T000/B000/{secret}", "slack")
        assert secret not in hint


class TestChannelTestEndpointRouting:
    """
    /channels/{id}/test must dispatch by channel type.

    It previously read every destination as a Slack webhook, so testing an email
    channel POSTed the subscriber's address at Slack and reported "Slack
    rejected the test message" — a failure with no relationship to the truth.
    """

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    class _Session:
        def __init__(self, row):
            self._row = row

        async def execute(self, *_a, **_kw):
            return TestChannelTestEndpointRouting._Result(self._row)

    async def _run(self, channel_type, destination):
        from app.api.v1.channels import test_channel

        ws = MagicMock()
        ws.id = uuid.uuid4()
        channel = _channel(ws.id, "ch", destination, channel_type=channel_type)

        with (
            # channels.py binds this name at import time, unlike the
            # dispatcher, which imports it inside the call.
            patch("app.api.v1.channels.get_credential_encryption", return_value=ENCRYPTION),
            patch(
                "app.services.notifications.email.EmailNotifier.send_test",
                new=AsyncMock(return_value=True),
            ) as email_test,
            patch(
                "app.services.notifications.slack.SlackNotifier.send_test",
                new=AsyncMock(return_value=True),
            ) as slack_test,
        ):
            result = await test_channel(
                channel_id=channel.id, session=self._Session(channel), workspace=ws
            )

        return result, email_test, slack_test

    @pytest.mark.asyncio
    async def test_email_channel_uses_the_email_notifier(self):
        result, email_test, slack_test = await self._run("email", "ops@example.com")

        assert result.delivered is True
        slack_test.assert_not_awaited()
        assert email_test.await_args.kwargs["recipient"] == "ops@example.com"

    @pytest.mark.asyncio
    async def test_slack_channel_still_uses_the_slack_notifier(self):
        result, email_test, slack_test = await self._run(
            "slack", "https://hooks.slack.com/services/T/B/x"
        )

        assert result.delivered is True
        email_test.assert_not_awaited()
        slack_test.assert_awaited_once()
