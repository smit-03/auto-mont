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


class ExecutionListParams(BaseModel):
    model_config = ConfigDict(strict=True)

    status: Literal["success", "error", "running", "timeout", "silent_fail"] | None = None
    integration_id: uuid.UUID | None = None
    workflow_id: str | None = None
    after: str | None = None  # Cursor for pagination
    limit: int = Field(default=50, ge=1, le=100)


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
