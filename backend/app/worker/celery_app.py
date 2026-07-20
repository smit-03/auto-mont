"""
Celery application factory.

Configures Celery with Redis broker and schedules periodic tasks.
"""

from celery import Celery

from app.config import settings

_celery_app: Celery | None = None


def get_celery_app() -> Celery:
    """Get or create the Celery application instance."""
    global _celery_app

    if _celery_app is None:
        _celery_app = Celery(
            "wrp",
            broker=settings.CELERY_BROKER_URL,
            backend=settings.CELERY_RESULT_BACKEND,
            include=[
                "app.worker.tasks.polling",
                "app.worker.tasks.heartbeat",
                "app.worker.tasks.credential",
            ],
        )

        # Configure Celery
        _celery_app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            timezone="UTC",
            enable_utc=True,
            task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
            task_track_started=True,
            task_time_limit=300,  # 5 minutes
            worker_prefetch_multiplier=1,
            worker_max_tasks_per_child=1000,
        )

        # Configure periodic tasks (beat schedule)
        _celery_app.conf.beat_schedule = {
            "heartbeat-check": {
                "task": "app.worker.tasks.heartbeat.check_all_monitors",
                "schedule": settings.HEARTBEAT_CHECK_INTERVAL_SECONDS,
                "options": {"queue": "beat"},
            },
            "credential-check": {
                "task": "app.worker.tasks.credential.check_all_credentials",
                "schedule": settings.CREDENTIAL_CHECK_INTERVAL_SECONDS,
                "options": {"queue": "beat"},
            },
            "poll-executions": {
                "task": "app.worker.tasks.polling.poll_all_integrations",
                "schedule": settings.DEFAULT_POLL_INTERVAL_SECONDS,
                "options": {"queue": "polling"},
            },
        }

    return _celery_app


# Create app instance for direct import
celery_app = get_celery_app()
