"""
Heartbeat check task - Periodic monitor health checks.

Task 15: Heartbeat engine + /ping/{token} public endpoint
Task 21: Celery beat schedule (heartbeat check 1min)
"""

import uuid

from celery import Task, shared_task
from sqlalchemy import select

from app.core.logging import get_logger
from app.database import get_session_factory
from app.models.monitor import Monitor
from app.services.alerts import build_dedup_key, record_alert
from app.services.heartbeat.engine import HeartbeatEngine
from app.services.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)


@shared_task(bind=True, name="app.worker.tasks.heartbeat.check_all_monitors")
def check_all_monitors(self: Task) -> dict:
    """
    Check all monitors for heartbeat violations.

    This is the beat-scheduled task that runs every minute.
    """
    from app.worker.loop import run_async

    return run_async(check_all_monitors_async())


async def check_all_monitors_async() -> dict:
    """
    Async implementation of monitor checking.

    Generates alerts for any missed heartbeats.
    """
    engine = HeartbeatEngine()
    dispatcher = NotificationDispatcher()

    async with get_session_factory().begin() as session:
        result = await session.execute(
            select(Monitor).where(
                Monitor.deleted_at.is_(None),
            )
        )
        monitors = result.scalars().all()

    results = {"total": len(monitors), "missed": 0, "errors": 0}

    for monitor in monitors:
        try:
            alert = await engine.check_monitor(monitor)
            if alert:
                # One incident per monitor outage, not one row per check. This
                # loop runs every 60s; without dedup at this layer a monitor
                # that stays down wrote an alert row every minute indefinitely.
                dedup_key = build_dedup_key("monitor", monitor.id, alert["category"], "miss")

                # Persist the alert record in its own transaction first, so a
                # notification failure below cannot roll back the alert row.
                async with get_session_factory().begin() as session:
                    recorded = await record_alert(
                        session,
                        workspace_id=uuid.UUID(alert["workspace_id"]),
                        dedup_key=dedup_key,
                        monitor_id=uuid.UUID(alert["monitor_id"]),
                        severity=alert["severity"],
                        category=alert["category"],
                        title=alert["title"],
                        description=alert["description"],
                        root_cause=alert["root_cause"],
                        suggested_fix=alert["suggested_fix"],
                    )
                    alert_payload = {
                        "id": str(recorded.id),
                        "workspace_id": alert["workspace_id"],
                        "title": alert["title"],
                        "description": alert["description"],
                        "severity": alert["severity"],
                        "suggested_fix": alert["suggested_fix"],
                    }

                # Send notification separately — failure here is logged by the
                # dispatcher and must not discard the already-committed alert.
                await dispatcher.send_alert(alert=alert_payload, dedup_key=dedup_key)

                if recorded.is_new:
                    results["missed"] += 1

        except Exception as e:
            logger.error(
                "heartbeat.monitor_check_failed",
                monitor_id=str(monitor.id),
                error=str(e),
            )
            results["errors"] += 1

    return results


# Heartbeat ping endpoint is in the API router
# (created separately in Task 15)
