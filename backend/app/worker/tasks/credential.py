"""
Credential check task - Periodic OAuth expiry checks.

Task 17: Credential model + Google OAuth expiry check logic
Task 21: Celery beat schedule (credential check 1h)
"""

from celery import Task, shared_task
from sqlalchemy import select, update

from app.core.logging import get_logger
from app.database import get_session_factory
from app.models.credential import Credential
from app.services.alerts import build_dedup_key, record_alert
from app.services.credential_vault.vault import get_vault
from app.services.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)


@shared_task(bind=True, name="app.worker.tasks.credential.check_all_credentials")
def check_all_credentials(self: Task) -> dict:
    """
    Check all credentials for expiry issues.

    This is the beat-scheduled task that runs hourly.
    """
    from app.worker.loop import run_async

    return run_async(check_all_credentials_async())


async def check_all_credentials_async() -> dict:
    """
    Async implementation of credential checking.

    Generates alerts for expiring/expired credentials.
    """
    vault = get_vault()
    dispatcher = NotificationDispatcher()

    async with get_session_factory().begin() as session:
        result = await session.execute(
            select(Credential).where(
                Credential.deleted_at.is_(None),
                Credential.status.in_(["active", "expiring"]),
            )
        )
        credentials = result.scalars().all()

    results = {"total": len(credentials), "alerts_generated": 0, "errors": 0}

    for credential in credentials:
        try:
            alerts = await vault.check_credential(credential)
            if alerts:
                for alert_data in alerts:
                    # Include the severity in the incident identity: a credential
                    # moving from "expiring soon" (warning) to "expired"
                    # (critical) is a genuine escalation and deserves its own
                    # incident and its own notification, not a silent counter
                    # bump on the earlier warning.
                    dedup_key = build_dedup_key(
                        "credential",
                        credential.id,
                        alert_data["category"],
                        alert_data["severity"],
                    )

                    # Persist the alert record first in its own transaction, so a
                    # notification failure below cannot roll back the alert row.
                    async with get_session_factory().begin() as session:
                        recorded = await record_alert(
                            session,
                            workspace_id=credential.workspace_id,
                            dedup_key=dedup_key,
                            credential_id=credential.id,
                            severity=alert_data["severity"],
                            category=alert_data["category"],
                            title=alert_data["title"],
                            description=alert_data["description"],
                            suggested_fix=alert_data.get("suggested_fix"),
                        )
                        alert_payload = {
                            "id": str(recorded.id),
                            "workspace_id": str(credential.workspace_id),
                            "title": alert_data["title"],
                            "description": alert_data["description"],
                            "severity": alert_data["severity"],
                            "suggested_fix": alert_data.get("suggested_fix"),
                        }

                    # Send notification separately — failure must not discard the
                    # already-committed alert record.
                    await dispatcher.send_alert(alert=alert_payload, dedup_key=dedup_key)

                    if recorded.is_new:
                        results["alerts_generated"] += 1

                # Determine new credential status from generated alerts
                new_status = credential.status
                if any(a["severity"] == "warning" for a in alerts):
                    new_status = "expiring"
                if any("expired" in a["title"].lower() for a in alerts):
                    new_status = "expired"

                # Persist via explicit UPDATE — credential is detached from the
                # bulk-query session, so mutating the attribute would not persist
                if new_status != credential.status:
                    async with get_session_factory().begin() as session:
                        await session.execute(
                            update(Credential)
                            .where(Credential.id == credential.id)
                            .values(status=new_status)
                        )

        except Exception as e:
            logger.error(
                "credential.check_failed",
                credential_id=str(credential.id),
                error=str(e),
            )
            results["errors"] += 1

    return results
