"""
Execution schemas - Pydantic models for execution trace queries.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    integration_id: uuid.UUID
    platform: str
    platform_run_id: str
    workflow_id: str
    workflow_name: str | None
    status: Literal["success", "error", "running", "timeout", "silent_fail"]
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    node_count: int | None
    items_processed: int | None
    error_message: str | None
    error_node: str | None
    outcome_valid: bool | None
    outcome_reason: str | None
    created_at: datetime


class ExecutionDetail(ExecutionRead):
    """
    A single execution including the platform's raw response.

    The list view deliberately omits raw_payload — it is unbounded (a large n8n
    run carries every node's input and output) and would dominate a 50-row page.
    The detail view is where node-level errors are inspected, so it pays that
    cost once.
    """

    raw_payload: dict


class ExecutionDiagnostic(BaseModel):
    model_config = ConfigDict(strict=True)

    execution_id: uuid.UUID
    run_now: bool = False


class DiagnosticResult(BaseModel):
    model_config = ConfigDict(strict=True)

    root_cause: str
    category: Literal["auth", "api", "logic", "schema", "infra", "ai", "silent", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_fix: str
    requires_hitl: bool = False
