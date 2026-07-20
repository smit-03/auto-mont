"""
Credential check task - Periodic OAuth expiry checks.

Task 17: Credential model + Google OAuth expiry check logic
Task 21: Celery beat schedule (credential check 1h)
"""

from celery import Task, shared_task
from sqlalchemy import select, update

from app.core.logging import get_logger
from app.database import get_session_factory
from app.models.alert import Alert
from app.models.credential import Credential
from app.services.credential_vault.vault import get_vault
from app.services.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)


@shared_task(bind=True, name="check_all_credentials")
def check_all_credentials(self: Task) -> dict:
    """
    Check all credentials for expiry issues.

    This is the beat-scheduled task that runs hourly.
    """
    import asyncio

    return asyncio.run(check_all_credentials_async())


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
                    db_alert = Alert(
                        workspace_id=credential.workspace_id,
                        credential_id=credential.id,
                        severity=alert_data["severity"],
                        category=alert_data["category"],
                        title=alert_data["title"],
                        description=alert_data["description"],
                        suggested_fix=alert_data.get("suggested_fix"),
                    )

                    # Persist the alert record first in its own transaction, so a
                    # notification failure below cannot roll back the alert row.
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

                    # Send notification separately — failure must not discard the
                    # already-committed alert record.
                    await dispatcher.send_alert(
                        alert=alert_payload,
                        dedup_key=f"credential:{credential.id}:{alert_data['category']}",
                    )

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
