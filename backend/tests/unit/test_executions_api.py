"""
Unit tests for the executions read API — Phase 2 deliverable 8.

Two concerns, and they fail in different ways:

  * The cursor. A cursor that round-trips wrongly does not error, it silently
    returns the wrong page — so these tests pin the encode/decode contract and
    the page-boundary arithmetic exactly.
  * The query. The session is faked, so no statement here reaches Postgres.
    TestQueryShape compiles the statement the endpoint actually built and
    asserts on the SQL; without it a query missing its workspace filter would
    pass every other test in this file and leak across tenants in production.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.pagination import decode_cursor, encode_cursor
from app.api.v1.executions import get_execution, get_execution_diagnostic, list_executions
from app.api.v1.integrations import list_integration_workflows
from app.models.execution import Execution


class FakeWorkspace:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        """Row-tuple result, as the workflow-listing aggregate query returns."""
        return self._rows


class FakeSession:
    """Returns a fixed row set and records the statement it was asked to run."""

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.statements = []

    async def execute(self, stmt=None, *_a, **_kw):
        self.statements.append(stmt)
        return FakeResult(self._rows)


BASE_TIME = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


def make_execution(
    *,
    workspace_id=None,
    seconds_ago: int = 0,
    status: str = "success",
    error_message: str | None = None,
    items_processed: int | None = 100,
) -> Execution:
    """An unpersisted Execution shaped like a row the list query would return."""
    return Execution(
        id=uuid.uuid4(),
        workspace_id=workspace_id or uuid.uuid4(),
        integration_id=uuid.uuid4(),
        platform="n8n",
        platform_run_id=f"run-{seconds_ago}",
        workflow_id="wf_orders",
        workflow_name="Order Sync",
        status=status,
        started_at=BASE_TIME,
        finished_at=BASE_TIME,
        duration_ms=1200,
        node_count=5,
        items_processed=items_processed,
        error_message=error_message,
        error_node=None,
        raw_payload={"data": {"resultData": {}}},
        outcome_valid=None,
        outcome_reason=None,
        created_at=BASE_TIME - timedelta(seconds=seconds_ago),
    )


def rendered_sql(stmt) -> str:
    """Compile a statement to literal Postgres SQL."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def call_list(session, workspace, **kwargs):
    """Call the list endpoint with the boilerplate filled in."""
    params = {
        "status": None,
        "integration_id": None,
        "workflow_id": None,
        "cursor": None,
        "limit": 50,
    }
    params.update(kwargs)
    return await list_executions(session=session, workspace=workspace, **params)


class TestCursorRoundTrip:
    """The cursor is opaque to clients but must be exact to us."""

    def test_encode_decode_preserves_both_components(self):
        created_at = datetime(2026, 8, 15, 13, 32, 22, 960123, tzinfo=UTC)
        row_id = uuid.uuid4()

        decoded_at, decoded_id = decode_cursor(encode_cursor(created_at, row_id))

        assert decoded_at == created_at
        assert decoded_id == row_id

    def test_microseconds_survive(self):
        """
        Two rows written by one polling pass differ only in microseconds. A
        cursor that truncates them resumes before rows it already returned.
        """
        a = datetime(2026, 8, 15, 13, 32, 22, 1, tzinfo=UTC)
        b = datetime(2026, 8, 15, 13, 32, 22, 2, tzinfo=UTC)
        row_id = uuid.uuid4()

        assert encode_cursor(a, row_id) != encode_cursor(b, row_id)
        assert decode_cursor(encode_cursor(a, row_id))[0] == a

    def test_cursor_is_url_safe_and_unpadded(self):
        cursor = encode_cursor(BASE_TIME, uuid.uuid4())

        assert "=" not in cursor
        assert "+" not in cursor
        assert "/" not in cursor

    def test_timezone_is_preserved(self):
        """A cursor decoded as naive would compare wrongly against timestamptz."""
        decoded_at, _ = decode_cursor(encode_cursor(BASE_TIME, uuid.uuid4()))

        assert decoded_at.tzinfo is not None


class TestCursorRejection:
    """A malformed cursor is a client error, never a silent reset to page one."""

    @pytest.mark.parametrize(
        "bad",
        [
            "not-base64!!",
            "",
            "YWJj",  # valid base64, no separator
            "MjAyNi0wOC0xNVQxMjowMDowMCswMDowMHxub3QtYS11dWlk",  # bad uuid
            "bm90LWEtZGF0ZXwxMjM0",  # bad timestamp
        ],
    )
    def test_unusable_cursors_raise_value_error(self, bad):
        with pytest.raises(ValueError):
            decode_cursor(bad)

    @pytest.mark.asyncio
    async def test_endpoint_returns_400_not_page_one(self):
        """
        Falling back to page one on a bad cursor would present as an infinite
        scroll that never advances — much harder to diagnose than a 400.
        """
        with pytest.raises(HTTPException) as exc:
            await call_list(FakeSession(), FakeWorkspace(), cursor="garbage!!")

        assert exc.value.status_code == 400


class TestPageBoundary:
    @pytest.mark.asyncio
    async def test_full_page_emits_cursor_from_the_last_returned_row(self):
        """
        The endpoint over-fetches by one to detect a next page. The cursor must
        name the last *returned* row, not the probe row — naming the probe would
        skip it on the following page.
        """
        workspace = FakeWorkspace()
        rows = [make_execution(workspace_id=workspace.id, seconds_ago=i) for i in range(3)]
        page = await call_list(FakeSession(rows), workspace, limit=2)

        assert len(page.items) == 2
        assert page.next_cursor is not None

        _, cursor_id = decode_cursor(page.next_cursor)
        assert cursor_id == rows[1].id

    @pytest.mark.asyncio
    async def test_exactly_one_page_has_no_next_cursor(self):
        """limit rows returned means the probe row was absent: this is the end."""
        workspace = FakeWorkspace()
        rows = [make_execution(workspace_id=workspace.id, seconds_ago=i) for i in range(2)]

        page = await call_list(FakeSession(rows), workspace, limit=2)

        assert len(page.items) == 2
        assert page.next_cursor is None

    @pytest.mark.asyncio
    async def test_empty_result_is_an_empty_page(self):
        page = await call_list(FakeSession([]), FakeWorkspace(), limit=50)

        assert page.items == []
        assert page.next_cursor is None

    @pytest.mark.asyncio
    async def test_over_fetches_exactly_one_row(self):
        session = FakeSession([])
        await call_list(session, FakeWorkspace(), limit=25)

        assert "LIMIT 26" in rendered_sql(session.statements[0])


class TestQueryShape:
    """
    Compile the statement the endpoint built and assert on the SQL. The faked
    session means a query that does not parse, or one missing its tenant filter,
    would otherwise pass every test above.
    """

    async def sql_for(self, **kwargs) -> str:
        workspace = FakeWorkspace()
        session = FakeSession([])
        await call_list(session, workspace, **kwargs)
        return rendered_sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_scopes_to_the_callers_workspace(self):
        workspace = FakeWorkspace()
        session = FakeSession([])
        await call_list(session, workspace)

        assert f"executions.workspace_id = '{workspace.id}'" in rendered_sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_excludes_soft_deleted_rows(self):
        assert "executions.deleted_at IS NULL" in await self.sql_for()

    @pytest.mark.asyncio
    async def test_orders_newest_first_with_id_as_tiebreak(self):
        """
        Without the id tiebreak, rows sharing a created_at have undefined order
        and a page boundary between them drops or repeats one.
        """
        sql = await self.sql_for()

        assert "ORDER BY executions.created_at DESC, executions.id DESC" in sql

    @pytest.mark.asyncio
    async def test_cursor_becomes_a_row_comparison_matching_the_order_by(self):
        row_id = uuid.uuid4()
        sql = await self.sql_for(cursor=encode_cursor(BASE_TIME, row_id))

        assert "(executions.created_at, executions.id) < (" in sql
        assert str(row_id) in sql

    @pytest.mark.asyncio
    async def test_no_cursor_means_no_keyset_predicate(self):
        assert "executions.created_at, executions.id) <" not in await self.sql_for()

    @pytest.mark.asyncio
    async def test_status_filter_is_applied(self):
        assert "executions.status = 'error'" in await self.sql_for(status="error")

    @pytest.mark.asyncio
    async def test_integration_filter_is_applied(self):
        integration_id = uuid.uuid4()
        sql = await self.sql_for(integration_id=integration_id)

        assert f"executions.integration_id = '{integration_id}'" in sql

    @pytest.mark.asyncio
    async def test_workflow_filter_is_applied(self):
        assert "executions.workflow_id = 'wf_orders'" in await self.sql_for(workflow_id="wf_orders")

    @pytest.mark.asyncio
    async def test_filters_combine_rather_than_replace(self):
        sql = await self.sql_for(status="error", workflow_id="wf_orders")

        assert "executions.status = 'error'" in sql
        assert "executions.workflow_id = 'wf_orders'" in sql

    @pytest.mark.asyncio
    async def test_unfiltered_query_constrains_nothing_extra(self):
        sql = await self.sql_for()

        assert "executions.status =" not in sql
        assert "executions.integration_id =" not in sql


class TestDetail:
    @pytest.mark.asyncio
    async def test_returns_the_raw_payload(self):
        """Node-level error display reads raw_payload; the list view omits it."""
        workspace = FakeWorkspace()
        row = make_execution(workspace_id=workspace.id)

        detail = await get_execution(
            execution_id=row.id, session=FakeSession([row]), workspace=workspace
        )

        assert detail.raw_payload == {"data": {"resultData": {}}}

    @pytest.mark.asyncio
    async def test_another_workspaces_execution_is_a_404(self):
        """
        The query filters on workspace_id, so a foreign id returns no row. A 404
        rather than a 403: confirming the id exists is itself a leak.
        """
        with pytest.raises(HTTPException) as exc:
            await get_execution(
                execution_id=uuid.uuid4(),
                session=FakeSession([]),
                workspace=FakeWorkspace(),
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_query_is_workspace_scoped(self):
        workspace = FakeWorkspace()
        session = FakeSession([make_execution(workspace_id=workspace.id)])
        execution_id = uuid.uuid4()

        await get_execution(execution_id=execution_id, session=session, workspace=workspace)
        sql = rendered_sql(session.statements[0])

        assert f"executions.workspace_id = '{workspace.id}'" in sql
        assert f"executions.id = '{execution_id}'" in sql
        assert "executions.deleted_at IS NULL" in sql


class TestWorkflowListing:
    """
    GET /integrations/{id}/workflows — lives in integrations.py but queries the
    executions table, so it is tested here alongside the other execution
    queries. It backs the monitor form's workflow picker.
    """

    async def sql_for_workflows(self, workspace, integration_id) -> str:
        session = FakeSession([])
        await list_integration_workflows(
            integration_id=integration_id, session=session, workspace=workspace
        )
        return rendered_sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_groups_by_workflow_so_each_appears_once(self):
        """A workflow that has run 500 times must be one option, not 500."""
        workspace = FakeWorkspace()
        sql = await self.sql_for_workflows(workspace, uuid.uuid4())

        assert "GROUP BY executions.workflow_id" in sql

    @pytest.mark.asyncio
    async def test_is_scoped_to_workspace_and_integration(self):
        workspace = FakeWorkspace()
        integration_id = uuid.uuid4()
        sql = await self.sql_for_workflows(workspace, integration_id)

        assert f"executions.workspace_id = '{workspace.id}'" in sql
        assert f"executions.integration_id = '{integration_id}'" in sql
        assert "executions.deleted_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_orders_most_recently_seen_first(self):
        """The workflow someone is debugging is the one that just ran."""
        sql = await self.sql_for_workflows(FakeWorkspace(), uuid.uuid4())

        assert "ORDER BY max(executions.created_at) DESC" in sql


class TestDiagnostic:
    @pytest.mark.asyncio
    async def test_diagnoses_a_stored_failure(self):
        workspace = FakeWorkspace()
        row = make_execution(
            workspace_id=workspace.id,
            status="error",
            error_message="invalid_grant: Token expired or revoked",
            items_processed=None,
        )

        result = await get_execution_diagnostic(
            execution_id=row.id, session=FakeSession([row]), workspace=workspace
        )

        assert result.category == "auth"
        assert result.confidence > 0.9

    @pytest.mark.asyncio
    async def test_silent_failure_is_diagnosed_despite_success_status(self):
        """A successful run that processed nothing is the product's core case."""
        workspace = FakeWorkspace()
        row = make_execution(workspace_id=workspace.id, status="success", items_processed=0)

        result = await get_execution_diagnostic(
            execution_id=row.id, session=FakeSession([row]), workspace=workspace
        )

        assert result.category == "silent"

    @pytest.mark.asyncio
    async def test_healthy_execution_has_no_diagnosis(self):
        """
        404 here means "nothing is wrong with this run", not "no such run". The
        alternative — a 200 carrying a synthetic all-clear — would put an
        unknown-category row in front of the user for a run that was fine.
        """
        workspace = FakeWorkspace()
        row = make_execution(workspace_id=workspace.id, status="success", items_processed=42)

        with pytest.raises(HTTPException) as exc:
            await get_execution_diagnostic(
                execution_id=row.id, session=FakeSession([row]), workspace=workspace
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_execution_is_a_404_before_diagnosing(self):
        with pytest.raises(HTTPException) as exc:
            await get_execution_diagnostic(
                execution_id=uuid.uuid4(),
                session=FakeSession([]),
                workspace=FakeWorkspace(),
            )

        assert exc.value.detail == "Execution not found"
