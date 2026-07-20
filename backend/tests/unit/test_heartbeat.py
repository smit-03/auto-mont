"""
Unit tests for Heartbeat Engine - Task 34.

Tests heartbeat miss detection with grace period boundary conditions.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.heartbeat.engine import HeartbeatEngine


class TestHeartbeatEngine:
    """Tests for the heartbeat dead-man's switch implementation."""

    @pytest.fixture
    def engine(self):
        return HeartbeatEngine()

    @pytest.mark.asyncio
    async def test_healthy_monitor_not_alerted(self, engine, mock_monitor_healthy):
        """Healthy monitor (recent ping) should not generate alert."""
        alert = await engine._check_heartbeat(mock_monitor_healthy)

        assert alert is None

    @pytest.mark.asyncio
    async def test_expired_monitor_generates_alert(self, engine, mock_monitor_expired):
        """Expired monitor (no recent ping) should generate critical alert."""
        alert = await engine._check_heartbeat(mock_monitor_expired)

        assert alert is not None
        assert alert["severity"] == "critical"
        assert alert["category"] == "silent"
        assert "missed" in alert["title"].lower()
        assert "ping" in alert["description"].lower()

    @pytest.mark.asyncio
    async def test_monitor_never_pinged_generates_alert(self, engine):
        """Monitor with no ping history should generate alert."""

        class MockMonitorNoPing:
            id = "mon_noping"
            workspace_id = "ws_123"
            name = "No Ping Monitor"
            monitor_type = "heartbeat"
            last_ping_at = None
            grace_period_s = 300

        alert = await engine._check_heartbeat(MockMonitorNoPing())

        assert alert is not None
        assert alert["severity"] == "critical"
        assert "no ping ever received" in alert["description"].lower()

    @pytest.mark.asyncio
    async def test_grace_period_boundary(self, engine):
        """Grace period boundary conditions."""

        # Ping at exactly grace_period_s ago - use slightly larger grace period
        # to account for timing differences between test setup and engine check
        class MockMonitorBoundary:
            id = "mon_boundary"
            workspace_id = "ws_123"
            name = "Boundary Monitor"
            monitor_type = "heartbeat"
            last_ping_at = datetime.now(UTC) - timedelta(seconds=300)
            grace_period_s = 301  # Slightly larger to account for timing

        alert = await engine._check_heartbeat(MockMonitorBoundary())

        # At exactly grace period, deadline is now, so no alert yet
        assert alert is None

    @pytest.mark.asyncio
    async def test_just_over_grace_period(self, engine):
        """Just over grace period should alert."""

        class MockMonitorOverdue:
            id = "mon_overdue"
            workspace_id = "ws_123"
            name = "Overdue Monitor"
            monitor_type = "heartbeat"
            last_ping_at = datetime.now(UTC) - timedelta(seconds=301)
            grace_period_s = 300

        alert = await engine._check_heartbeat(MockMonitorOverdue())

        assert alert is not None

    @pytest.mark.asyncio
    async def test_alert_has_required_fields(self, engine, mock_monitor_expired):
        """Alert should have all required fields."""
        alert = await engine._check_heartbeat(mock_monitor_expired)

        assert "workspace_id" in alert
        assert "monitor_id" in alert
        assert "severity" in alert
        assert "category" in alert
        assert "title" in alert
        assert "description" in alert
        assert "root_cause" in alert
        assert "suggested_fix" in alert


class TestHeartbeatAlertContent:
    """Tests for alert content quality."""

    @pytest.fixture
    def engine(self):
        return HeartbeatEngine()

    @pytest.mark.asyncio
    async def test_alert_mentions_workflow_trigger(self, engine, mock_monitor_expired):
        """Alert should mention workflow/trigger issues as cause."""
        alert = await engine._check_heartbeat(mock_monitor_expired)

        # Root cause should mention trigger possibilities
        cause = alert["root_cause"].lower()
        assert any(x in cause for x in ["trigger", "workflow", "process", "socket"])

    @pytest.mark.asyncio
    async def test_alert_suggests_verification(self, engine, mock_monitor_expired):
        """Alert should suggest checking workflow is active."""
        alert = await engine._check_heartbeat(mock_monitor_expired)

        fix = alert["suggested_fix"].lower()
        assert "active" in fix or "check" in fix
