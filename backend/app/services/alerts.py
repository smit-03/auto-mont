"""
Alert lifecycle — incident-level deduplication.

Previously every producer did ``session.add(Alert(...))`` on every detection, and
deduplication lived only in the notification dispatcher's Redis check. That
suppressed duplicate *messages* but not duplicate *rows*: a monitor that stayed
down wrote one alert per beat tick — once a minute, forever — while Slack
correctly saw one message per hour. The alerts table grew without bound and the
dashboard feed became a thousand copies of one incident.

Dedup belongs here, above persistence, and in Postgres rather than Redis. Redis
is a cache: an evicted key costs you a duplicate notification, which is
tolerable, but it must not cost you a duplicate *incident*.

The Redis throttle in the dispatcher is retained and re-purposed — it now
governs how often an *ongoing* incident re-notifies, which is a different
question from whether the incident is new.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.alert import Alert

logger = get_logger(__name__)


def build_dedup_key(*parts: str | uuid.UUID | None) -> str:
    """
    Build a stable incident identity from its distinguishing attributes.

    Deliberately excludes anything that varies per detection — notably a
    platform run id, which would make every occurrence its own "incident" and
    defeat the whole mechanism.
    """
    return ":".join(str(p) for p in parts if p)


@dataclass
class RecordedAlert:
    """Outcome of recording a detection against the alerts table."""

    id: uuid.UUID
    occurrence_count: int
    is_new: bool

    @property
    def notification_payload_id(self) -> str:
        return str(self.id)


async def record_alert(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    dedup_key: str,
    severity: str,
    category: str,
    title: str,
    description: str,
    root_cause: str | None = None,
    suggested_fix: str | None = None,
    integration_id: uuid.UUID | None = None,
    monitor_id: uuid.UUID | None = None,
    credential_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
) -> RecordedAlert:
    """
    Record a detection, collapsing it into the open incident if one exists.

    Atomic: a single INSERT ... ON CONFLICT against the partial unique index on
    (workspace_id, dedup_key) WHERE resolved_at IS NULL. Two workers detecting
    the same failure concurrently produce one incident, not two — a
    check-then-insert would race here exactly as the execution upsert does.
    """
    now = datetime.now(UTC)

    stmt = (
        pg_insert(Alert)
        .values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            dedup_key=dedup_key,
            severity=severity,
            category=category,
            title=title,
            description=description,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            integration_id=integration_id,
            monitor_id=monitor_id,
            credential_id=credential_id,
            execution_id=execution_id,
            occurrence_count=1,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "dedup_key"],
            index_where=Alert.resolved_at.is_(None),
            set_={
                "occurrence_count": Alert.occurrence_count + 1,
                "last_seen_at": now,
                # Refresh the human-readable fields: the latest detection has
                # the most current error text for the same underlying incident.
                "description": description,
                "severity": severity,
            },
        )
        .returning(Alert.id, Alert.occurrence_count)
    )

    result = await session.execute(stmt)
    row = result.first()

    # occurrence_count is 1 only on a fresh insert; the update branch always
    # increments to 2 or more.
    alert_id, count = (row[0], row[1]) if row else (uuid.uuid4(), 1)
    is_new = count == 1

    logger.info(
        "alert.recorded",
        alert_id=str(alert_id),
        dedup_key=dedup_key,
        occurrence_count=count,
        is_new=is_new,
    )

    return RecordedAlert(id=alert_id, occurrence_count=count, is_new=is_new)


async def resolve_alerts_for_monitor(session: AsyncSession, monitor_id: uuid.UUID) -> int:
    """
    Close any open incident for a monitor. Returns the number resolved.

    Called when a heartbeat ping arrives: the monitor is demonstrably alive, so
    the incident is over. Without this the partial unique index would keep the
    original incident open forever, and a monitor that failed, recovered, and
    failed again would silently fold the second outage into the first.
    """
    from sqlalchemy import update

    result = await session.execute(
        update(Alert)
        .where(Alert.monitor_id == monitor_id, Alert.resolved_at.is_(None))
        .values(resolved_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)
