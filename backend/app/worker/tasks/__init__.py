"""
Celery tasks package.
"""

from app.worker.tasks.credential import check_all_credentials
from app.worker.tasks.heartbeat import check_all_monitors
from app.worker.tasks.polling import poll_all_integrations, poll_integration_executions

# Import task for Celery task registry
from app.worker.tasks.heartbeat import check_all_monitors as check_all_monitors_task

__all__ = [
    "poll_all_integrations",
    "poll_integration_executions",
    "check_all_monitors",
    "check_all_credentials",
]
