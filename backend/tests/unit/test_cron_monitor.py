"""
Unit tests for the cron schedule check.

A cron monitor asks a question a heartbeat monitor cannot express: "did the run
scheduled for 09:00 actually happen?" The boundaries that matter are therefore
the scheduled fire time and the grace period after it, not elapsed time since
the last ping.

Time is frozen throughout. The interesting assertions are all a fixed offset
from a scheduled fire time, so a real clock would make them pass or fail
depending on which second the suite happened to run in.
"""

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from app.services.heartbeat.engine import HeartbeatEngine

# 09:05 UTC — five minutes after the 09:00 run that every case below is about.
NOW = datetime(2026, 8, 19, 9, 5, 0, tzinfo=UTC)
SCHEDULED = datetime(2026, 8, 19, 9, 0, 0, tzinfo=UTC)
DAILY_9AM = "0 9 * * *"


def make_cron_monitor(
    *,
    expected_cron: str | None = DAILY_9AM,
    last_ping_at: datetime | None = None,
    created_at: datetime | None = None,
    grace_period_s: int = 300,
    alert_on_miss: bool = True,
):
    """Build a monitor stub carrying the attributes the engine reads."""

    class MockMonitor:
        pass

    monitor = MockMonitor()
    monitor.id = "mon_cron"
    monitor.workspace_id = "ws_123"
    monitor.name = "Nightly Sync"
    monitor.monitor_type = "cron"
    monitor.expected_cron = expected_cron
    monitor.last_ping_at = last_ping_at
    monitor.created_at = created_at or (NOW - timedelta(days=30))
    monitor.grace_period_s = grace_period_s
    monitor.alert_on_miss = alert_on_miss
    monitor.last_status = "healthy"
    return monitor


@freeze_time(NOW)
class TestCronSchedule:
    @pytest.fixture
    def engine(self):
        return HeartbeatEngine()

    @pytest.mark.asyncio
    async def test_no_expression_never_alerts(self, engine):
        """Without a schedule there is no scheduled run to have missed."""
        monitor = make_cron_monitor(expected_cron=None)

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_ping_after_scheduled_run_is_healthy(self, engine):
        """A ping recorded after the scheduled fire time satisfies it."""
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED + timedelta(seconds=30),
            grace_period_s=60,
        )

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_missed_run_past_grace_alerts(self, engine):
        """A scheduled run with no ping, past its grace period, alerts."""
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=60,
        )

        alert = await engine._check_cron_schedule(monitor)

        assert alert is not None
        assert alert["severity"] == "critical"
        assert alert["category"] == "silent"
        assert "scheduled run missed" in alert["title"].lower()
        assert "0 9 * * *" in alert["description"]

    @pytest.mark.asyncio
    async def test_within_grace_period_does_not_alert(self, engine):
        """
        A run due five minutes ago with a ten-minute grace is not yet late.

        Without this the monitor would alert at the instant of every scheduled
        fire time, before the workflow could possibly have pinged.
        """
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=600,
        )

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_grace_period_boundary_is_inclusive_of_lateness(self, engine):
        """
        At exactly grace_period_s past the scheduled run, the run is late.

        NOW is 300s after SCHEDULED, so a 300s grace expires precisely now.
        """
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=300,
        )

        assert await engine._check_cron_schedule(monitor) is not None

    @pytest.mark.asyncio
    async def test_one_second_before_grace_expiry_is_silent(self, engine):
        """One second inside the grace period is still not late."""
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=301,
        )

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_never_pinged_uses_created_at(self, engine):
        """
        A monitor created after the last scheduled run has missed nothing.

        Falling back to the epoch instead would make every newly created cron
        monitor alert immediately.
        """
        monitor = make_cron_monitor(
            last_ping_at=None,
            created_at=SCHEDULED + timedelta(seconds=1),
            grace_period_s=60,
        )

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_never_pinged_and_run_was_due_alerts(self, engine):
        """A monitor that predates a missed run still alerts on it."""
        monitor = make_cron_monitor(
            last_ping_at=None,
            created_at=SCHEDULED - timedelta(days=1),
            grace_period_s=60,
        )

        alert = await engine._check_cron_schedule(monitor)

        assert alert is not None
        assert "never" in alert["description"].lower()

    @pytest.mark.asyncio
    async def test_invalid_expression_is_silent_not_alerting(self, engine):
        """
        A malformed expression is a monitor bug, not a workflow failure.

        Alerting would blame the customer's workflow for our own parse failure.
        """
        monitor = make_cron_monitor(
            expected_cron="not a cron expression",
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=60,
        )

        assert await engine._check_cron_schedule(monitor) is None

    @pytest.mark.asyncio
    async def test_naive_timestamps_do_not_raise(self, engine):
        """
        A naive last_ping_at must not raise inside the beat tick.

        Comparing naive to aware datetimes raises TypeError, which would abort
        the tick for every other monitor in the workspace, not just this one.
        """
        monitor = make_cron_monitor(
            last_ping_at=(SCHEDULED - timedelta(days=1)).replace(tzinfo=None),
            grace_period_s=60,
        )

        assert await engine._check_cron_schedule(monitor) is not None

    @pytest.mark.asyncio
    async def test_check_monitor_routes_cron_type(self, engine):
        """check_monitor dispatches cron monitors to the schedule check."""
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=60,
        )

        assert await engine.check_monitor(monitor) is not None

    @pytest.mark.asyncio
    async def test_alert_on_miss_false_silences_cron(self, engine):
        """The mute flag applies to cron monitors as it does to heartbeats."""
        monitor = make_cron_monitor(
            last_ping_at=SCHEDULED - timedelta(days=1),
            grace_period_s=60,
            alert_on_miss=False,
        )

        assert await engine.check_monitor(monitor) is None

    @pytest.mark.asyncio
    async def test_schema_type_is_no_longer_dispatched(self, engine):
        """
        The "schema" monitor type was removed.

        Workflow schema drift is detected in the polling path against a stored
        fingerprint, so a schema monitor duplicated a shipped detector. This
        guards against the type being reintroduced without a check behind it.
        """
        monitor = make_cron_monitor(last_ping_at=None)
        monitor.monitor_type = "schema"

        assert await engine.check_monitor(monitor) is None
