"""
Heartbeat Engine - Dead-man's switch implementation.

Checks if expected events fired within grace period.
Generates critical alerts when heartbeats are missed.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from croniter import croniter

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.models.monitor import Monitor

logger = get_logger(__name__)


class HeartbeatEngine:
    """
    Dead-man's switch implementation.

    The engine checks: did the expected event fire within the grace period?
    If not, it generates a critical alert. This catches the IMAP silent drop,
    cron drift, and trigger disconnection failures identified in the research.
    """

    async def check_monitor(self, monitor: "Monitor") -> dict | None:
        """Check monitor health and return alert dict if violated."""
        # Respect the mute flag — a monitor with alert_on_miss=False is silenced.
        if not monitor.alert_on_miss:
            return None
        if monitor.monitor_type == "heartbeat":
            return await self._check_heartbeat(monitor)
        elif monitor.monitor_type == "cron":
            return await self._check_cron_schedule(monitor)
        elif monitor.monitor_type == "outcome":
            return await self._check_outcome_assertion(monitor)
        return None

    async def _check_heartbeat(self, monitor: "Monitor") -> dict | None:
        """Check heartbeat monitor for missed pings."""
        if not monitor.last_ping_at:
            return self._create_heartbeat_alert(
                monitor=monitor,
                reason="No ping ever received",
            )

        deadline = monitor.last_ping_at + timedelta(seconds=monitor.grace_period_s)

        if datetime.now(UTC) >= deadline:
            return self._create_heartbeat_alert(
                monitor=monitor,
                reason=f"No ping received within {monitor.grace_period_s}s grace period",
            )

        return None

    async def _check_cron_schedule(self, monitor: "Monitor") -> dict | None:
        """
        Alert when a scheduled run's ping never arrived.

        A heartbeat monitor asks "has anything pinged recently?", which cannot
        express "this should fire at 09:00 daily" — a 24h grace period tolerates
        a run that is a full day late, and a 1h one alerts every night. This
        check instead walks back to the most recent scheduled fire time and asks
        whether a ping arrived for *that* occurrence.

        The reference point when a monitor has never been pinged is its own
        `created_at`, not the epoch: a monitor created two minutes before its
        next scheduled run has not missed anything yet, and alerting on it would
        make every new cron monitor fire once on creation.
        """
        if not monitor.expected_cron:
            return None

        now = datetime.now(UTC)
        # `last_ping_at` is timestamptz, but a monitor built in-memory (tests,
        # or a row created before the column was populated) can carry a naive
        # datetime. Comparing naive to aware raises TypeError, which in the beat
        # task would silently kill the whole tick for every other monitor.
        since = monitor.last_ping_at or monitor.created_at
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

        try:
            previous_run = croniter(monitor.expected_cron, now).get_prev(datetime)
        except (ValueError, KeyError) as exc:
            # An unparseable expression is a configuration error, not a missed
            # run. Alerting here would report the workflow as broken when the
            # monitor is what is broken; staying silent would hide it entirely.
            logger.warning(
                "cron.invalid_expression",
                monitor_id=str(monitor.id),
                expected_cron=monitor.expected_cron,
                error=str(exc),
            )
            return None

        if previous_run.tzinfo is None:
            previous_run = previous_run.replace(tzinfo=UTC)

        # Nothing was due since we started watching.
        if since is not None and since >= previous_run:
            return None

        # The run was due but the grace period has not elapsed yet — a workflow
        # scheduled for 09:00 is not late at 09:00:01.
        if now < previous_run + timedelta(seconds=monitor.grace_period_s):
            return None

        return self._create_heartbeat_alert(
            monitor=monitor,
            reason=(
                f"No ping received for the run scheduled at "
                f"{previous_run.isoformat()} "
                f"(cron '{monitor.expected_cron}', "
                f"{monitor.grace_period_s}s grace period). "
                f"Last ping: {monitor.last_ping_at or 'never'}."
            ),
        )

    async def _check_outcome_assertion(self, monitor: "Monitor") -> dict | None:
        """
        Not evaluated here — outcome assertions run in the polling path.

        `check_outcome_assertions()` is called from `_diagnose_and_alert` with
        the execution being ingested, so every run is evaluated exactly once.
        This engine's beat tick would only ever see whichever run happened to be
        latest when it fired, missing a bad run followed by a good one. See the
        2026-08-16 entry in DECISIONS.md.
        """
        return None

    def _create_heartbeat_alert(self, monitor: "Monitor", reason: str) -> dict:
        """Create alert payload for heartbeat violation."""
        # A cron monitor missing its 09:00 run is a missed *scheduled run*, not
        # a missed heartbeat. The dedup key is unchanged (it is monitor-scoped),
        # so this only affects what the alert reads as.
        noun = "Scheduled run missed" if monitor.monitor_type == "cron" else "Heartbeat missed"
        return {
            "workspace_id": str(monitor.workspace_id),
            "monitor_id": str(monitor.id),
            "severity": "critical",
            "category": "silent",
            "title": f"{noun}: {monitor.name}",
            "description": reason,
            "root_cause": (
                "The workflow or process monitoring this trigger has not executed "
                "within the expected window. Possible causes: IMAP trigger silently "
                "disconnected, cron schedule drifted, container restarted, or "
                "workflow was manually paused."
            ),
            "suggested_fix": (
                "Check your workflow is active and the trigger is connected. "
                "For IMAP triggers, verify the TCP socket is alive. "
                "Review recent infrastructure events."
            ),
        }
