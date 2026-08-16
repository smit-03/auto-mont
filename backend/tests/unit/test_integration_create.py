"""
Unit tests for idempotent integration creation (create_integration endpoint).

Exercises app.api.v1.integrations.create_integration directly against a fake
AsyncSession, mirroring the FakeSession pattern used in
tests/integration/test_polling_to_slack.py. No real database is needed: the
DB-level partial unique index (uq_integrations_workspace_platform_name_active,
see alembic/versions/20260722_000003_add_integration_unique_constraint.py) is
what actually enforces uniqueness in production; here we simulate the
IntegrityError it raises on a duplicate active (workspace_id, platform,
display_name) and verify the endpoint converts it to a 409 instead of a 500.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.v1.integrations import create_integration
from app.schemas.integration import IntegrationCreate


class FakeWorkspace:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class FakeSession:
    """Minimal async session; `commit_raises` controls the constraint outcome."""

    def __init__(self, commit_raises: Exception | None = None) -> None:
        self.commit_raises = commit_raises
        self.added: list = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self.commit_raises is not None:
            raise self.commit_raises
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, obj) -> None:
        self.refreshed = True
        # Simulate DB-assigned defaults a real refresh() would populate.
        import datetime as _dt

        obj.id = uuid.uuid4()
        obj.status = "active"
        obj.polling_enabled = True
        obj.config = {}
        obj.created_at = _dt.datetime.now(_dt.UTC)
        obj.deleted_at = None


def _payload() -> IntegrationCreate:
    return IntegrationCreate(
        display_name="Test Failing WF-1",
        platform="n8n",
        api_key="a-valid-test-key",
    )


@pytest.mark.asyncio
async def test_create_integration_success():
    """A normal create commits once and returns the serialized integration."""
    session = FakeSession()
    workspace = FakeWorkspace()

    result = await create_integration(_payload(), session=session, workspace=workspace)

    assert session.committed is True
    assert session.rolled_back is False
    assert result.display_name == "Test Failing WF-1"
    assert result.platform == "n8n"


@pytest.mark.asyncio
async def test_create_integration_duplicate_active_returns_409_not_500():
    """A duplicate active (workspace, platform, display_name) is a 409, not a 500.

    Simulates the partial unique index raising IntegrityError on commit —
    this is the scenario that previously slipped through as an uncaught 500
    and produced duplicate rows (see DECISIONS.md 2026-07-22 cleanup entry).
    """
    session = FakeSession(commit_raises=IntegrityError("duplicate key", None, None))
    workspace = FakeWorkspace()

    with pytest.raises(HTTPException) as exc_info:
        await create_integration(_payload(), session=session, workspace=workspace)

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.detail
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_create_integration_after_soft_delete_succeeds():
    """Recreating with the same (workspace, platform, display_name) succeeds
    once the prior row is soft-deleted.

    The partial unique index only covers rows where deleted_at IS NULL, so a
    soft-deleted duplicate does not block a new insert at the DB layer. The
    endpoint has no application-level duplicate check of its own, so this is
    just the success path again — asserted explicitly so a future
    application-level pre-check can't accidentally reintroduce a block here.
    """
    session = FakeSession()
    workspace = FakeWorkspace()

    result = await create_integration(_payload(), session=session, workspace=workspace)

    assert session.committed is True
    assert result.display_name == "Test Failing WF-1"


@pytest.mark.asyncio
async def test_concurrent_create_race_second_request_gets_409():
    """Two concurrent creates for the same (workspace, platform, display_name):
    one wins the DB constraint, the other gets a clean 409.

    Each request has its own AsyncSession in the real app (per-request
    dependency), so the race is modeled with two independent FakeSessions:
    the first's commit succeeds, the second's raises IntegrityError as it
    would when it loses the unique-index race in Postgres.
    """
    workspace = FakeWorkspace()
    winner_session = FakeSession()
    loser_session = FakeSession(commit_raises=IntegrityError("duplicate key", None, None))

    winner_result = await create_integration(
        _payload(), session=winner_session, workspace=workspace
    )
    assert winner_result.display_name == "Test Failing WF-1"
    assert winner_session.committed is True

    with pytest.raises(HTTPException) as exc_info:
        await create_integration(_payload(), session=loser_session, workspace=workspace)

    assert exc_info.value.status_code == 409
    assert loser_session.rolled_back is True
