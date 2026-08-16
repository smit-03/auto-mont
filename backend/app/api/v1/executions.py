"""
Execution endpoints - Query execution traces and diagnostics.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import get_current_workspace, get_db_session
from app.api.pagination import CursorPage, decode_cursor, encode_cursor
from app.models.execution import Execution
from app.models.workspace import Workspace
from app.schemas.execution import DiagnosticResult, ExecutionDetail, ExecutionRead
from app.services.diagnostic.rule_engine import DiagnosticRuleEngine

router = APIRouter()

ExecutionStatus = Literal["success", "error", "running", "timeout", "silent_fail"]

# Rules are immutable once compiled, so one engine is shared by every request.
_rule_engine = DiagnosticRuleEngine()


def _keyset_predicate(cursor: str) -> ColumnElement[bool]:
    """
    Turn a cursor into the WHERE clause that resumes after it.

    Row comparison rather than `created_at < x OR (created_at = x AND id < y)`:
    it says the same thing, and it matches the (created_at, id) ordering the
    index is built for. Lives outside the endpoint because `status` is a query
    parameter name there, which shadows the module needed to raise a 400.
    """
    try:
        created_at, row_id = decode_cursor(cursor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cursor",
        ) from None

    return tuple_(Execution.created_at, Execution.id) < tuple_(literal(created_at), literal(row_id))


async def _get_owned_execution(
    execution_id: uuid.UUID, workspace: Workspace, session: AsyncSession
) -> Execution:
    """Fetch an execution scoped to the workspace or raise 404."""
    result = await session.execute(
        select(Execution).where(
            Execution.id == execution_id,
            Execution.workspace_id == workspace.id,
            Execution.deleted_at.is_(None),
        )
    )
    execution = result.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return execution


@router.get("", response_model=CursorPage[ExecutionRead])
async def list_executions(
    status: ExecutionStatus | None = None,
    integration_id: uuid.UUID | None = None,
    workflow_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> CursorPage[ExecutionRead]:
    """List executions for the current workspace, newest first."""
    query = select(Execution).where(
        Execution.workspace_id == workspace.id,
        Execution.deleted_at.is_(None),
    )

    if status is not None:
        query = query.where(Execution.status == status)
    if integration_id is not None:
        query = query.where(Execution.integration_id == integration_id)
    if workflow_id is not None:
        query = query.where(Execution.workflow_id == workflow_id)
    if cursor is not None:
        query = query.where(_keyset_predicate(cursor))

    # id breaks ties on created_at. Without it two rows written by the same
    # polling pass share a timestamp, their relative order is undefined, and a
    # page boundary landing between them drops or repeats one.
    query = query.order_by(Execution.created_at.desc(), Execution.id.desc())

    # One extra row answers "is there another page?" without a second COUNT
    # query, and is discarded before serialization.
    result = await session.execute(query.limit(limit + 1))
    rows = list(result.scalars().all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)

    return CursorPage(
        items=[ExecutionRead.model_validate(e) for e in rows],
        next_cursor=next_cursor,
    )


@router.get("/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> ExecutionDetail:
    """Get a single execution including the platform's raw response."""
    execution = await _get_owned_execution(execution_id, workspace, session)
    return ExecutionDetail.model_validate(execution)


@router.get("/{execution_id}/diagnostic", response_model=DiagnosticResult)
async def get_execution_diagnostic(
    execution_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    workspace: Workspace = Depends(get_current_workspace),
) -> DiagnosticResult:
    """
    Re-run the rule engine against a stored execution.

    Diagnoses are computed rather than stored, so an execution ingested before a
    rule existed is diagnosed by the current table. A 404 means the engine found
    nothing wrong — a successful run that also processed items has no diagnosis,
    which is the expected answer, not a missing record.
    """
    execution = await _get_owned_execution(execution_id, workspace, session)

    match = _rule_engine.diagnose(
        {
            "error_message": execution.error_message,
            "status": execution.status,
            "items_processed": execution.items_processed,
        }
    )
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No diagnosis available for this execution",
        )

    return DiagnosticResult(
        root_cause=match.root_cause,
        category=match.category,
        confidence=match.confidence,
        suggested_fix=match.suggested_fix,
        requires_hitl=match.requires_hitl,
    )
