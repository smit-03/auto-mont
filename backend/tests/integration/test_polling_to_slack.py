"""
Integration test: polling -> diagnosis -> alert -> Slack delivery.

Task 35: End-to-end validation of the poll -> alert -> notify pipeline.

External boundaries are mocked, real components run:
- n8n HTTP is mocked at the httpx client, so the REAL N8NAdapter parsing runs.
- Redis is replaced with an in-memory cache (Redis is not required to run this).
- The Slack webhook HTTP POST is mocked, so the REAL Block Kit build + dispatch runs.
- The DB session factory is replaced with an in-memory fake (no live DB needed).

The final test (test_production_task_end_to_end) calls the REAL production task
poll_integration_executions_async, proving the D7 wiring — not in-test chaining.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.alert import Alert
from app.services.diagnostic.rule_engine import DiagnosticRuleEngine
from app.services.notifications.dispatcher import NotificationDispatcher
from app.services.notifications.slack import SlackNotifier
from app.services.sentinel.n8n_adapter import N8NAdapter

# Severity is not part of RuleMatch; the glue task derives it. Mirror that
# mapping here so the alert handed to Slack is realistic.
_CATEGORY_SEVERITY = {"auth": "critical", "api": "warning", "silent": "critical", "unknown": "warning"}


class InMemoryCache:
    """Duck-typed stand-in for RedisCache used by the dispatcher's dedup."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self.store[key] = value
        return True


def _n8n_payload(error_message: str) -> dict:
    """A single n8n execution page containing one errored run."""
    return {
        "data": [
            {
                "id": "exec_integ_1",
                "finished": True,
                "mode": "trigger",
                "startedAt": "2026-07-18T00:00:00.000Z",
                "stoppedAt": "2026-07-18T00:00:02.000Z",
                "workflowId": "wf_test",
                "workflowName": "Test Workflow",
                "status": "error",
                "data": {"resultData": {"error": {"message": error_message, "name": "NodeApiError"}}},
            }
        ]
    }


def _recent_n8n_payload(error_message: str) -> dict:
    """A single n8n execution page with a run timestamped just now.

    Used by tests that go through poll_integration_executions_async, whose
    `since` filter (last 5 minutes) would drop a fixed historical timestamp.
    """
    now = datetime.now(UTC)
    return {
        "data": [
            {
                "id": "exec_integ_1",
                "finished": True,
                "mode": "trigger",
                "startedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "stoppedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "workflowId": "wf_test",
                "workflowName": "Test Workflow",
                "status": "error",
                "data": {"resultData": {"error": {"message": error_message, "name": "NodeApiError"}}},
            }
        ]
    }


def _mock_n8n_adapter(payload: dict) -> N8NAdapter:
    """Build a real N8NAdapter whose HTTP client is mocked to return `payload`."""
    adapter = N8NAdapter(base_url="http://n8n.test", api_key="test-key")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=payload)
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    adapter._client = mock_client  # bypass real httpx client creation
    return adapter


class FakeSession:
    """Minimal async session capturing added rows; no real DB required."""

    def __init__(self, added: list) -> None:
        self._added = added
        # No existing executions -> every polled run is treated as new.
        self._empty_result = MagicMock()
        self._empty_result.scalar_one_or_none = MagicMock(return_value=None)

    async def execute(self, *_args, **_kwargs):
        return self._empty_result

    def add(self, obj) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        # Assign an id to any Alert so the payload build has one.
        import uuid as _uuid

        for obj in self._added:
            if getattr(obj, "id", None) is None:
                obj.id = _uuid.uuid4()


class FakeSessionFactory:
    """Stands in for get_session_factory(); .begin() yields a FakeSession."""

    def __init__(self) -> None:
        self.added: list = []

    def begin(self):  # noqa: ANN201 - async context manager
        added = self.added

        class _Ctx:
            async def __aenter__(self_inner):
                return FakeSession(added)

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.mark.integration
class TestPollToAlertToSlack:
    """Drive the real adapter -> rule engine -> dispatcher -> Slack chain."""

    @pytest.mark.asyncio
    async def test_invalid_grant_poll_generates_slack_alert(self):
        """n8n invalid_grant error -> AUTH_001 -> critical alert -> Slack POST."""
        adapter = _mock_n8n_adapter(_n8n_payload("invalid_grant: token revoked"))

        # 1. Poll: real adapter parsing of the mocked n8n response.
        normalized = [
            e async for e in adapter.list_recent_executions(
                since=datetime(2026, 7, 18, tzinfo=UTC), limit=100
            )
        ]
        assert len(normalized) == 1
        assert normalized[0].error_message == "invalid_grant: token revoked"

        # 2. Diagnose: real rule engine over the normalized execution.
        exec_dict = {
            "error_message": normalized[0].error_message,
            "status": normalized[0].status,
            "items_processed": normalized[0].items_processed,
        }
        match = DiagnosticRuleEngine().diagnose(exec_dict)
        assert match is not None
        assert match.rule_id == "AUTH_001"
        assert match.category == "auth"

        # 3. Build alert (test-side glue standing in for D7).
        alert = {
            "id": "alert_1",
            "workspace_id": "ws_integ",
            "title": f"{match.category}: {match.root_cause}",
            "description": match.root_cause,
            "severity": _CATEGORY_SEVERITY[match.category],
            "suggested_fix": match.suggested_fix,
        }

        # 4. Dispatch: real dispatcher + real Slack Block Kit, mocked webhook POST.
        cache = InMemoryCache()
        dispatcher = NotificationDispatcher(cache=cache)
        dispatcher.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")

        slack_response = MagicMock()
        slack_response.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=slack_response)) as mock_post:
            delivered = await dispatcher.send_alert(
                alert=alert, dedup_key="integration:ws_integ:auth"
            )

        assert delivered is True
        mock_post.assert_awaited_once()
        # The real Block Kit payload was built and posted to the webhook.
        _, kwargs = mock_post.call_args
        body = kwargs["json"]
        assert body["text"].startswith("[CRITICAL]")
        assert any(
            block.get("type") == "header" for block in body["blocks"]
        )

    @pytest.mark.asyncio
    async def test_duplicate_alert_suppressed_within_window(self):
        """Second identical alert is deduped; Slack is only POSTed once."""
        cache = InMemoryCache()
        dispatcher = NotificationDispatcher(cache=cache)
        dispatcher.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")

        alert = {
            "id": "alert_dup",
            "workspace_id": "ws_integ",
            "title": "auth: OAuth refresh token expired",
            "description": "OAuth refresh token expired",
            "severity": "critical",
            "suggested_fix": "Re-authenticate.",
        }

        slack_response = MagicMock()
        slack_response.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=slack_response)) as mock_post:
            first = await dispatcher.send_alert(alert=alert, dedup_key="integration:ws_integ:auth")
            second = await dispatcher.send_alert(alert=alert, dedup_key="integration:ws_integ:auth")

        assert first is True
        assert second is True  # deduped returns True (already handled)
        mock_post.assert_awaited_once()  # only the first actually delivered

    @pytest.mark.asyncio
    async def test_silent_failure_poll_generates_alert(self):
        """success + zero items processed -> SILENT_001 -> Slack POST."""
        exec_dict = {"status": "success", "items_processed": 0, "error_message": None}
        match = DiagnosticRuleEngine().diagnose(exec_dict)
        assert match is not None
        assert match.rule_id == "SILENT_001"
        assert match.category == "silent"

        alert = {
            "id": "alert_silent",
            "workspace_id": "ws_integ",
            "title": "silent failure",
            "description": match.root_cause,
            "severity": _CATEGORY_SEVERITY[match.category],
            "suggested_fix": match.suggested_fix,
        }

        cache = InMemoryCache()
        dispatcher = NotificationDispatcher(cache=cache)
        dispatcher.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")

        slack_response = MagicMock()
        slack_response.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=slack_response)) as mock_post:
            delivered = await dispatcher.send_alert(
                alert=alert, dedup_key="integration:ws_integ:silent"
            )

        assert delivered is True
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_slack_delivery_failure_does_not_dedup(self):
        """If Slack delivery fails, the alert is NOT suppressed for next time (F5)."""
        cache = InMemoryCache()
        dispatcher = NotificationDispatcher(cache=cache)
        dispatcher.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")

        alert = {
            "id": "alert_fail",
            "workspace_id": "ws_integ",
            "title": "auth: token expired",
            "description": "token expired",
            "severity": "critical",
            "suggested_fix": "Re-authenticate.",
        }

        # First send: Slack raises -> not delivered, dedup key must NOT be set.
        with patch(
            "httpx.AsyncClient.post", new=AsyncMock(side_effect=RuntimeError("slack down"))
        ):
            first = await dispatcher.send_alert(alert=alert, dedup_key="integration:ws_integ:auth")
        assert first is False
        assert cache.store == {}  # F5: no dedup key set on failure

        # Retry once Slack recovers: must actually deliver, not be suppressed.
        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=ok_response)) as mock_post:
            retry = await dispatcher.send_alert(alert=alert, dedup_key="integration:ws_integ:auth")
        assert retry is True
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_failure_falls_back_to_generic_alert(self):
        """An unmatched failed execution should still create an UNKNOWN alert and post to Slack."""
        import uuid

        from app.worker.tasks import polling

        integration = polling.Integration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            platform="n8n",
            display_name="Test n8n",
            base_url="http://n8n.test",
            last_polled_at=None,
        )

        adapter = _mock_n8n_adapter(_recent_n8n_payload("dns lookup failed for host example.test"))
        fake_factory = FakeSessionFactory()
        cache = InMemoryCache()

        slack_response = MagicMock()
        slack_response.raise_for_status = MagicMock()

        def _dispatcher_factory():
            d = NotificationDispatcher(cache=cache)
            d.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")
            return d

        with (
            patch.object(polling, "get_session_factory", return_value=fake_factory),
            patch.object(polling, "_get_adapter", return_value=adapter),
            patch.object(polling, "NotificationDispatcher", _dispatcher_factory),
            patch("httpx.AsyncClient.post", new=AsyncMock(return_value=slack_response)) as mock_post,
        ):
            fetched = await polling.poll_integration_executions_async(integration)

        assert fetched == 1
        alerts = [obj for obj in fake_factory.added if isinstance(obj, Alert)]
        assert len(alerts) == 1
        assert alerts[0].category == "unknown"
        assert alerts[0].severity == "warning"
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_production_task_end_to_end(self):
        """
        Call the REAL production task poll_integration_executions_async (D7 wiring):
        n8n poll -> upsert -> diagnose -> Alert created -> Slack POSTed.

        Only external boundaries are faked: the DB session factory, the adapter's
        httpx client, Redis (in-memory cache), and the Slack webhook POST.
        """
        import uuid

        from app.worker.tasks import polling

        # Real integration ORM object (not persisted; only its attrs are read).
        integration = polling.Integration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            platform="n8n",
            display_name="Test n8n",
            base_url="http://n8n.test",
            last_polled_at=None,
        )

        adapter = _mock_n8n_adapter(_recent_n8n_payload("invalid_grant: token revoked"))
        fake_factory = FakeSessionFactory()
        cache = InMemoryCache()

        slack_response = MagicMock()
        slack_response.raise_for_status = MagicMock()

        # Force the dispatcher to use our in-memory cache + a configured Slack.
        def _dispatcher_factory():
            d = NotificationDispatcher(cache=cache)
            d.slack = SlackNotifier(webhook_url="https://hooks.slack.test/xxx")
            return d

        with (
            patch.object(polling, "get_session_factory", return_value=fake_factory),
            patch.object(polling, "_get_adapter", return_value=adapter),
            patch.object(polling, "NotificationDispatcher", _dispatcher_factory),
            patch("httpx.AsyncClient.post", new=AsyncMock(return_value=slack_response)) as mock_post,
        ):
            fetched = await polling.poll_integration_executions_async(integration)

        # Ingestion happened.
        assert fetched == 1
        # An Alert row was created by the real task (AUTH_001 -> critical).
        alerts = [obj for obj in fake_factory.added if isinstance(obj, Alert)]
        assert len(alerts) == 1
        assert alerts[0].category == "auth"
        assert alerts[0].severity == "critical"
        # And Slack was actually delivered by the real dispatcher.
        mock_post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_production_task_success_execution_no_alert(self):
        """A healthy execution (success, items>0) produces no alert."""
        import uuid

        from app.worker.tasks import polling

        integration = polling.Integration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            platform="n8n",
            display_name="Test n8n",
            base_url="http://n8n.test",
            last_polled_at=None,
        )

        now = datetime.now(UTC)
        healthy_payload = {
            "data": [
                {
                    "id": "exec_ok",
                    "finished": True,
                    "startedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "stoppedAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "workflowId": "wf_ok",
                    "workflowName": "Healthy Workflow",
                    "status": "success",
                    "data": {"resultData": {"runData": {"Node": [[{"json": {}}]]}}},
                }
            ]
        }
        adapter = _mock_n8n_adapter(healthy_payload)
        fake_factory = FakeSessionFactory()

        with (
            patch.object(polling, "get_session_factory", return_value=fake_factory),
            patch.object(polling, "_get_adapter", return_value=adapter),
            patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post,
        ):
            fetched = await polling.poll_integration_executions_async(integration)

        assert fetched == 1
        assert [obj for obj in fake_factory.added if isinstance(obj, Alert)] == []
        mock_post.assert_not_awaited()

