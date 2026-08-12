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

from app.core.security import CredentialEncryption
from app.models.channel import NotificationChannel
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


def _channel(workspace_id, name, url, min_severity="warning"):
    """Build an unpersisted channel with a correctly encrypted destination."""
    return NotificationChannel(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        channel_type="slack",
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

        assert resolved == [("a", "https://a.test")]

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

        assert [n for n, _ in warning] == ["all"]
        assert [n for n, _ in critical] == ["all", "crit-only"]

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

        assert resolved == [("good", "https://good.test")]


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
            channels=[("ops", "https://ops.test"), ("oncall", "https://oncall.test")],
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
        monkeypatch.setattr(
            disp.settings, "SLACK_WEBHOOK_URL", "https://dev.test", raising=False
        )

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
            cache=InMemoryCache(), channels=[("ops", "https://ops.test")]
        )
        dispatcher.slack = SlackNotifier()

        with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
            delivered = await dispatcher.send_alert(alert={**self.ALERT, "workspace_id": None})

        assert delivered is False
        post.assert_not_awaited()
