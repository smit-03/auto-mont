"""
Heartbeat Engine - Dead-man's switch implementation.

Checks if expected events fired within grace period.
Generates critical alerts when heartbeats are missed.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
        elif monitor.monitor_type == "schema":
            return await self._check_schema_stability(monitor)
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
        """Check cron schedule for drift."""
        if not monitor.expected_cron or not monitor.last_ping_at:
            return None

        # Expected: implement cron parsing and next-run calculation
        # For Phase 1, we just check if last ping is too old relative to cron
        # A proper implementation would use croniter to calculate expected next run

        # Placeholder - full implementation in Phase 2
        return None

    async def _check_outcome_assertion(self, monitor: "Monitor") -> dict | None:
        """Check outcome assertion (min_records, etc.)."""
        # Placeholder - full implementation in Phase 2
        return None

    async def _check_schema_stability(self, monitor: "Monitor") -> dict | None:
        """Check schema stability for workflow."""
        # Placeholder - full implementation in Phase 2
        return None

    def _create_heartbeat_alert(self, monitor: "Monitor", reason: str) -> dict:
        """Create alert payload for heartbeat violation."""
        return {
            "workspace_id": str(monitor.workspace_id),
            "monitor_id": str(monitor.id),
            "severity": "critical",
            "category": "silent",
            "title": f"Heartbeat missed: {monitor.name}",
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
