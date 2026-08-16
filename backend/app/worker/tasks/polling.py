"""
Polling task - Periodic execution fetching from integrated platforms.

Task 11: poll_integration_executions (periodic, 60s)
Task 12: Execution normalization + upsert to DB
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery import Task, shared_task
from sqlalchemy import select, update

from app.config import settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.core.security import get_credential_encryption
from app.database import get_session_factory
from app.models.execution import Execution
from app.models.integration import Integration
from app.services.alerts import build_dedup_key, record_alert
from app.services.diagnostic.baseline import (
    BASELINE_WINDOW_DAYS,
    BaselineAnomaly,
    check_baseline_anomalies,
)
from app.services.diagnostic.outcome import OutcomeViolation, check_outcome_assertions
from app.services.diagnostic.rule_engine import DiagnosticRuleEngine, RuleMatch
from app.services.diagnostic.schema_checker import SchemaDrift, build_drift_fix, check_schema_drift
from app.services.notifications.dispatcher import NotificationDispatcher
from app.services.sentinel.base_adapter import BaseAdapter, NormalizedExecution
from app.services.sentinel.n8n_adapter import N8NAdapter

logger = get_logger(__name__)

# The rule engine returns a category but not a severity; derive severity from
# the category so diagnosis alerts carry the right urgency (auth/infra/silent
# failures are critical, everything else is a warning).
_CATEGORY_SEVERITY = {
    "auth": "critical",
    "infra": "critical",
    "silent": "critical",
    "api": "warning",
    "schema": "warning",
    "logic": "warning",
    "ai": "warning",
    "unknown": "warning",
}


def _get_adapter(integration: Integration) -> BaseAdapter:
    """Get platform-specific adapter for an integration."""
    if integration.platform == "n8n":
        # Decrypt the stored API key; fall back to the configured default n8n
        # key (Phase 1 single-instance/dev path) when no key is stored.
        if integration.api_key_enc:
            encryption = get_credential_encryption()
            api_key = encryption.decrypt(integration.api_key_enc, str(integration.workspace_id))
        elif settings.N8N_API_KEY:
            api_key = settings.N8N_API_KEY
        else:
            raise IntegrationError("No n8n API key available for integration", "n8n")
        return N8NAdapter(
            base_url=integration.base_url or settings.N8N_BASE_URL or "http://localhost:5678",
            api_key=api_key,
        )
    else:
        raise IntegrationError(f"Unknown platform: {integration.platform}", integration.platform)


@shared_task(bind=True, name="app.worker.tasks.polling.poll_all_integrations")
def poll_all_integrations(self: Task) -> dict:
    """
    Poll all active integrations for new executions.

    This is the beat-scheduled task that triggers polling for all
    integrations with polling_enabled=true.
    """
    from app.worker.loop import run_async

    async def _poll() -> dict:
        now = datetime.now(UTC)

        async with get_session_factory().begin() as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.polling_enabled.is_(True),
                    Integration.deleted_at.is_(None),
                    Integration.status.in_(["active", "error"]),
                )
            )
            candidates = result.scalars().all()

        # Respect each integration's configured poll_interval_s. Without this the
        # setting is collected in the UI, validated by the API, stored on the row
        # — and then ignored, because this beat task polls everything on one
        # global schedule.
        #
        # Effective granularity is the beat interval: an integration is polled on
        # the first tick at or after its interval elapses, so a 90s interval on a
        # 60s beat polls at ~120s. Filtering in Python is fine at current scale;
        # push it into the WHERE clause when the integration count grows.
        integrations = [
            i
            for i in candidates
            if i.last_polled_at is None
            or (now - i.last_polled_at).total_seconds() >= i.poll_interval_s
        ]

        results = {"total": len(integrations), "polled": 0, "errors": 0}

        for integration in integrations:
            try:
                count = await poll_integration_executions_async(integration)
                results["polled"] += count
            except Exception as e:
                logger.error(
                    "polling.integration_failed",
                    integration_id=str(integration.id),
                    error=str(e),
                )
                results["errors"] += 1

        return results

    return run_async(_poll())


@shared_task(bind=True, name="app.worker.tasks.polling.poll_integration_executions")
def poll_integration_executions(self: Task, integration_id: str) -> int:
    """
    Poll a specific integration for executions.

    Args:
        integration_id: UUID of the integration to poll

    Returns:
        Number of executions fetched
    """
    from app.worker.loop import run_async

    async def _poll_one() -> int:
        async with get_session_factory().begin() as session:
            result = await session.execute(
                select(Integration).where(Integration.id == uuid.UUID(integration_id))
            )
            integration = result.scalar_one_or_none()
            if integration is None:
                logger.error("polling.integration_not_found", integration_id=integration_id)
                return 0
        return await poll_integration_executions_async(integration)

    return run_async(_poll_one())


async def poll_integration_executions_async(integration: Integration) -> int:
    """
    Async implementation of execution polling.

    Normalizes and upserts executions to database.
    """
    logger.info(
        "polling.started",
        integration_id=str(integration.id),
        platform=integration.platform,
    )

    try:
        adapter = _get_adapter(integration)
    except IntegrationError as e:
        logger.error("polling.adapter_error", error=str(e))
        return 0

    # Calculate since timestamp
    since = datetime.now(UTC) - timedelta(minutes=5)
    if integration.last_polled_at:
        since = max(since, integration.last_polled_at)

    fetched_count = 0
    # Executions to diagnose after the ingestion transaction commits, paired with
    # the primary key of the row each one was written to. We diagnose post-commit
    # so a notification failure can't roll back ingested rows (F6).
    #
    # The id is carried through rather than looked up again because the diagnosis
    # runs in a different transaction: without it every alert this pipeline
    # produces has a NULL execution_id, which is what made an alert impossible to
    # trace back to the run that caused it.
    to_diagnose: list[tuple[NormalizedExecution, uuid.UUID]] = []

    try:
        async with get_session_factory().begin() as session:
            async for normalized in adapter.list_recent_executions(since=since, limit=100):
                # Upsert execution
                stmt = select(Execution).where(
                    Execution.integration_id == integration.id,
                    Execution.platform_run_id == normalized.platform_run_id,
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # Refresh every field that can change as a run progresses.
                    # error_message / items_processed were previously omitted, so
                    # a run first seen as "running" kept a NULL error message
                    # forever once it failed.
                    for attr in [
                        "status",
                        "workflow_name",
                        "finished_at",
                        "duration_ms",
                        "node_count",
                        "items_processed",
                        "error_message",
                        "error_node",
                        "raw_payload",
                    ]:
                        setattr(existing, attr, getattr(normalized, attr, None))
                else:
                    # Create new. The primary key is assigned here rather than
                    # left to the column default so the diagnosis pass can
                    # reference the row without a flush-and-refresh round trip
                    # per execution.
                    execution_id = uuid.uuid4()
                    execution = Execution(
                        id=execution_id,
                        workspace_id=integration.workspace_id,
                        integration_id=integration.id,
                        platform=integration.platform,
                        platform_run_id=normalized.platform_run_id,
                        workflow_id=normalized.workflow_id,
                        workflow_name=normalized.workflow_name,
                        status=normalized.status,
                        started_at=normalized.started_at,
                        finished_at=normalized.finished_at,
                        duration_ms=normalized.duration_ms,
                        node_count=normalized.node_count,
                        items_processed=normalized.items_processed,
                        error_message=normalized.error_message,
                        error_node=normalized.error_node,
                        raw_payload=normalized.raw_payload,
                    )
                    session.add(execution)
                    # Only diagnose brand-new executions to avoid re-alerting on
                    # every poll for the same run.
                    #
                    # Safe against the insert losing the unique-constraint race
                    # with a concurrent poll: that raises on commit, the
                    # exception propagates out of this function, and the
                    # diagnosis pass below never runs — so an id that was rolled
                    # back is never used as a foreign key.
                    to_diagnose.append((normalized, execution_id))

                fetched_count += 1

            # Update last polled timestamp (explicit UPDATE — integration may be
            # detached from this session, so mutating the attribute would not
            # persist)
            await session.execute(
                update(Integration)
                .where(Integration.id == integration.id)
                .values(last_polled_at=datetime.now(UTC))
            )
    finally:
        # One adapter (and one httpx connection pool) is built per poll cycle;
        # without this the worker leaks a pool every beat tick.
        await adapter.close()

    # Diagnose newly-ingested executions and dispatch alerts for any matches.
    alerts_generated = await _diagnose_and_alert(integration, to_diagnose)

    logger.info(
        "polling.completed",
        integration_id=str(integration.id),
        fetched=fetched_count,
        alerts=alerts_generated,
    )

    return fetched_count


@dataclass
class _Detection:
    """One thing worth alerting about, from any of the three detectors."""

    dedup_key: str
    severity: str
    category: str
    title: str
    description: str
    root_cause: str | None
    suggested_fix: str | None
    # Only outcome assertions carry one: the alert names the monitor whose rule
    # was broken, so the reader can go straight to the assertion to judge
    # whether the workflow or the assertion is what is out of date.
    monitor_id: uuid.UUID | None = None


def _rule_detection(
    integration: Integration, normalized: NormalizedExecution, match: RuleMatch
) -> _Detection:
    """Detection from the static signature rule engine."""
    label = normalized.workflow_name or normalized.workflow_id
    return _Detection(
        # Incident identity excludes platform_run_id. Including it — as this
        # previously did — made every execution its own incident, so the same
        # recurring failure never deduplicated against itself.
        dedup_key=build_dedup_key("execution", integration.id, match.category, match.rule_id),
        severity=_CATEGORY_SEVERITY.get(match.category, "warning"),
        category=match.category,
        title=f"{match.category.title()} failure: {label}",
        description=match.root_cause,
        root_cause=match.root_cause,
        suggested_fix=match.suggested_fix,
    )


def _drift_detection(
    integration: Integration, normalized: NormalizedExecution, drift: SchemaDrift
) -> _Detection:
    """Detection from schema drift between consecutive successful runs."""
    label = normalized.workflow_name or normalized.workflow_id
    return _Detection(
        # Per workflow, not per fingerprint: a workflow whose shape keeps
        # changing is one ongoing incident, not a new one every poll.
        dedup_key=build_dedup_key("schema_drift", integration.id, drift.workflow_id),
        severity=drift.severity,
        category="schema",
        title=f"Schema drift: {label}",
        description=drift.describe(),
        root_cause=(
            "The output shape of this workflow changed between two consecutive "
            "successful runs. Both runs reported success, so nothing else would "
            "have surfaced this."
        ),
        suggested_fix=build_drift_fix(drift),
    )


def _outcome_detection(
    integration: Integration, normalized: NormalizedExecution, violation: OutcomeViolation
) -> _Detection:
    """Detection from an outcome assertion monitor."""
    label = normalized.workflow_name or normalized.workflow_id
    return _Detection(
        # Per monitor, not per failed assertion: one monitor's broken promise is
        # one incident, however many of its assertions broke together. Fixing
        # the workflow resolves all of them at once.
        dedup_key=build_dedup_key("outcome", integration.id, str(violation.monitor_id)),
        severity=violation.severity,
        category="silent",
        title=f"Outcome assertion failed: {label}",
        description=violation.describe(),
        root_cause=(
            "This run reported success, but its output does not satisfy the "
            "assertions configured for this workflow. A successful run producing "
            "wrong output is the failure class that has no error message."
        ),
        suggested_fix=violation.suggested_fix(),
        monitor_id=violation.monitor_id,
    )


def _anomaly_detection(
    integration: Integration, normalized: NormalizedExecution, anomaly: BaselineAnomaly
) -> _Detection:
    """Detection from the rolling percentile baseline."""
    label = normalized.workflow_name or normalized.workflow_id
    kind = "Volume" if anomaly.metric == "items_processed" else "Duration"
    return _Detection(
        # Direction is part of the identity: a workflow oscillating above and
        # below its band has two distinct problems, not one.
        dedup_key=build_dedup_key(
            "baseline", integration.id, anomaly.workflow_id, anomaly.metric, anomaly.direction
        ),
        severity=anomaly.severity,
        category="silent",
        title=f"{kind} anomaly: {label}",
        description=anomaly.describe(),
        root_cause=(
            f"This run's {anomaly.metric} deviates from the workflow's own rolling "
            f"{BASELINE_WINDOW_DAYS}-day baseline. The execution reported success "
            f"and produced no error."
        ),
        suggested_fix=anomaly.suggested_fix(),
    )


async def _diagnose_and_alert(
    integration: Integration, executions: list[tuple[NormalizedExecution, uuid.UUID]]
) -> int:
    """
    Run each newly-ingested execution through all three Phase 2 detectors and
    dispatch an alert for anything they find.

    Detection and alert persistence share one transaction, deliberately. Schema
    drift is destructive to detect: the check advances the stored baseline, so a
    drift observed but not recorded is a drift that can never be observed again.
    Committing the advanced baseline and the alert row together makes that
    all-or-nothing.

    Notifications are sent after the transaction closes, mirroring the
    heartbeat/credential pattern: a Slack outage must not roll back an alert
    record (F6).
    """
    engine = DiagnosticRuleEngine()
    dispatcher = NotificationDispatcher()
    alerts_generated = 0

    for normalized, execution_id in executions:
        pending: list[tuple[_Detection, str]] = []

        async with get_session_factory().begin() as session:
            detections: list[_Detection] = []

            # Tier 1 — static error signatures. No database access.
            match = engine.diagnose(
                {
                    "error_message": normalized.error_message,
                    "status": normalized.status,
                    "items_processed": normalized.items_processed,
                }
            )
            if match:
                detections.append(_rule_detection(integration, normalized, match))

            # Tier 2 — schema drift against the stored fingerprint.
            drift = await check_schema_drift(
                session,
                workspace_id=integration.workspace_id,
                integration_id=integration.id,
                platform=integration.platform,
                workflow_id=normalized.workflow_id,
                platform_run_id=normalized.platform_run_id,
                status=normalized.status,
                raw_payload=normalized.raw_payload,
            )
            if drift:
                detections.append(_drift_detection(integration, normalized, drift))

            # Tier 3 — deviation from the workflow's own rolling baseline.
            for anomaly in await check_baseline_anomalies(
                session,
                integration_id=integration.id,
                workflow_id=normalized.workflow_id,
                platform_run_id=normalized.platform_run_id,
                status=normalized.status,
                items_processed=normalized.items_processed,
                duration_ms=normalized.duration_ms,
            ):
                detections.append(_anomaly_detection(integration, normalized, anomaly))

            # Tier 4 — customer-stated assertions about this workflow's output.
            for violation in await check_outcome_assertions(
                session,
                workspace_id=integration.workspace_id,
                integration_id=integration.id,
                platform=integration.platform,
                workflow_id=normalized.workflow_id,
                status=normalized.status,
                items_processed=normalized.items_processed,
                raw_payload=normalized.raw_payload,
            ):
                detections.append(_outcome_detection(integration, normalized, violation))

            for detection in detections:
                recorded = await record_alert(
                    session,
                    workspace_id=integration.workspace_id,
                    dedup_key=detection.dedup_key,
                    integration_id=integration.id,
                    # Every detector in this pass fires on one specific run, so
                    # all three kinds of alert link back to it. Note the incident
                    # keeps the id of the run that *opened* it: subsequent
                    # occurrences collapse into the same row via the dedup key
                    # rather than repointing it at the newest run.
                    execution_id=execution_id,
                    monitor_id=detection.monitor_id,
                    severity=detection.severity,
                    category=detection.category,
                    title=detection.title,
                    description=detection.description,
                    root_cause=detection.root_cause,
                    suggested_fix=detection.suggested_fix,
                )
                pending.append((detection, str(recorded.id)))
                if recorded.is_new:
                    alerts_generated += 1

        # Transaction closed. The dispatcher's Redis throttle decides whether an
        # ongoing incident re-notifies; the rows above are already durable.
        for detection, alert_id in pending:
            await dispatcher.send_alert(
                alert={
                    "id": alert_id,
                    "workspace_id": str(integration.workspace_id),
                    "title": detection.title,
                    "description": detection.description,
                    "severity": detection.severity,
                    "suggested_fix": detection.suggested_fix,
                },
                dedup_key=detection.dedup_key,
            )

    return alerts_generated
