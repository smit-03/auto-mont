"""
Platform Adapter Interface - Abstract base class for all platform integrations.

Every platform integration must conform to this interface.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime


@dataclass
class NormalizedExecution:
    """Normalized execution record from any platform."""

    platform_run_id: str
    workflow_id: str
    workflow_name: str | None
    status: str  # success | error | running | timeout | silent_fail
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    node_count: int | None
    items_processed: int | None
    error_message: str | None
    error_node: str | None
    raw_payload: dict


class BaseAdapter(ABC):
    """Abstract base class for platform-specific adapters."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @abstractmethod
    async def test_connectivity(self) -> bool:
        """Verify API key is valid and platform is reachable."""

    @abstractmethod
    def list_recent_executions(
        self,
        since: datetime,
        limit: int = 100,
    ) -> AsyncIterator[NormalizedExecution]:
        """Yield normalized executions since the given timestamp."""

    @abstractmethod
    async def get_execution_detail(
        self,
        platform_run_id: str,
    ) -> NormalizedExecution:
        """Fetch full execution detail including node-level data."""

    @abstractmethod
    async def list_workflow_credentials(self) -> list[dict]:
        """List connected OAuth credentials with metadata."""

    async def close(self) -> None:
        """
        Release any resources held by the adapter (HTTP connection pools etc.).

        Deliberately concrete, not abstract: adapters that hold no client are
        correct to do nothing here, so subclasses should not be forced to
        implement it. Adapters holding a client override it. Callers must
        invoke this, since one adapter is constructed per poll cycle.
        """
        return None

    def normalize_status(self, platform_status: str) -> str:
        """
        Map platform-specific status to normalized status.

        Subclasses override this for their platform's status values.
        """
        status_map = {
            "success": "success",
            "finished": "success",
            "error": "error",
            "failed": "error",
            "running": "running",
            "waiting": "running",
            "timeout": "timeout",
            "cancelled": "error",
        }
        return status_map.get(platform_status.lower(), "running")
