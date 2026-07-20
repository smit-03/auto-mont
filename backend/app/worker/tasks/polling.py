"""
Polling task - Periodic execution fetching from integrated platforms.

Task 11: poll_integration_executions (periodic, 60s)
Task 12: Execution normalization + upsert to DB
"""

import uuid
from datetime import UTC, datetime, timedelta

from celery import Task, shared_task
from sqlalchemy import select, update

from app.config import settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.core.security import get_credential_encryption
from app.database import get_session_factory
from app.models.alert import Alert
from app.models.execution import Execution
from app.models.integration import Integration
from app.services.diagnostic.rule_engine import DiagnosticRuleEngine
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
}


def _get_adapter(integration: Integration) -> BaseAdapter:
    """Get platform-specific adapter for an integration."""
    if integration.platform == "n8n":
        # Decrypt the stored API key; fall back to the configured default n8n
        # key (Phase 1 single-instance/dev path) when no key is stored.
        if integration.api_key_enc:
            encryption = get_credential_encryption()
            api_key = encryption.decrypt(
                integration.api_key_enc, str(integration.workspace_id)
            )
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


@shared_task(bind=True, name="poll_all_integrations")
def poll_all_integrations(self: Task) -> dict:
    """
    Poll all active integrations for new executions.

    This is the beat-scheduled task that triggers polling for all
    integrations with polling_enabled=true.
    """
    import asyncio

    async def _poll() -> dict:
        async with get_session_factory().begin() as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.polling_enabled.is_(True),
                    Integration.deleted_at.is_(None),
                    Integration.status.in_(["active", "error"]),
                )
            )
            integrations = result.scalars().all()

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

    return asyncio.run(_poll())


@shared_task(bind=True, name="poll_integration_executions")
def poll_integration_executions(self: Task, integration_id: str) -> int:
    """
    Poll a specific integration for executions.

    Args:
        integration_id: UUID of the integration to poll

    Returns:
        Number of executions fetched
    """
    import asyncio

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

    return asyncio.run(_poll_one())


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
    # Executions to diagnose after the ingestion transaction commits. We diagnose
    # post-commit so a notification failure can't roll back ingested rows (F6).
    to_diagnose: list[NormalizedExecution] = []

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
                # Update existing
                for attr in ["status", "finished_at", "duration_ms", "raw_payload"]:
                    setattr(existing, attr, getattr(normalized, attr, None))
            else:
                # Create new
                execution = Execution(
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
                to_diagnose.append(normalized)

            fetched_count += 1

        # Update last polled timestamp (explicit UPDATE — integration may be
        # detached from this session, so mutating the attribute would not persist)
        await session.execute(
            update(Integration)
            .where(Integration.id == integration.id)
            .values(last_polled_at=datetime.now(UTC))
        )

    # Diagnose newly-ingested executions and dispatch alerts for any matches.
    alerts_generated = await _diagnose_and_alert(integration, to_diagnose)

    logger.info(
        "polling.completed",
        integration_id=str(integration.id),
        fetched=fetched_count,
        alerts=alerts_generated,
    )

    return fetched_count


async def _diagnose_and_alert(
    integration: Integration, executions: list[NormalizedExecution]
) -> int:
    """
    Run each execution through the rule engine; create + dispatch an alert for
    any match. Mirrors the heartbeat/credential pattern: the alert row is
    committed in its own transaction first, then the notification is sent
    separately so a delivery failure cannot roll back the alert record (F6).
    """
    engine = DiagnosticRuleEngine()
    dispatcher = NotificationDispatcher()
    alerts_generated = 0

    for normalized in executions:
        exec_dict = {
            "error_message": normalized.error_message,
            "status": normalized.status,
            "items_processed": normalized.items_processed,
        }
        match = engine.diagnose(exec_dict)
        if not match:
            continue

        severity = _CATEGORY_SEVERITY.get(match.category, "warning")
        title = f"{match.category.title()} failure: {normalized.workflow_name or normalized.workflow_id}"

        db_alert = Alert(
            workspace_id=integration.workspace_id,
            integration_id=integration.id,
            severity=severity,
            category=match.category,
            title=title,
            description=match.root_cause,
            root_cause=match.root_cause,
            suggested_fix=match.suggested_fix,
        )

        # Persist the alert record in its own transaction first (F6).
        async with get_session_factory().begin() as session:
            session.add(db_alert)
            await session.flush()
            alert_payload = {
                "id": str(db_alert.id),
                "workspace_id": str(db_alert.workspace_id),
                "title": db_alert.title,
                "description": db_alert.description,
                "severity": db_alert.severity,
                "suggested_fix": db_alert.suggested_fix,
            }

        # Send notification separately — failure must not discard the alert row.
        await dispatcher.send_alert(
            alert=alert_payload,
            dedup_key=f"execution:{integration.id}:{normalized.platform_run_id}:{match.rule_id}",
        )
        alerts_generated += 1

    return alerts_generated
