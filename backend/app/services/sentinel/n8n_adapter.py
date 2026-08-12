"""
n8n platform adapter - API client for n8n workflow executions.

Implements BaseAdapter interface with httpx async client.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from app.core.exceptions import ExternalServiceError, IntegrationError
from app.core.logging import get_logger
from app.services.sentinel.base_adapter import BaseAdapter, NormalizedExecution

logger = get_logger(__name__)


class N8NAdapter(BaseAdapter):
    """n8n platform adapter with async httpx client."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        super().__init__(base_url, api_key)
        self._client: httpx.AsyncClient | None = None
        self.timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "X-N8N-API-KEY": self.api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def test_connectivity(self) -> bool:
        """Test connection to n8n API."""
        try:
            client = await self._get_client()
            response = await client.get("/api/v1/workflows")
            return response.status_code == 200
        except Exception as e:
            logger.error("n8n.connectivity_test_failed", error=str(e))
            return False

    async def list_recent_executions(
        self,
        since: datetime,
        limit: int = 100,
    ) -> AsyncIterator[NormalizedExecution]:
        """List executions since the given timestamp.

        n8n's public API has no server-side date filter (no `lastStartedAfter`)
        and no offset-based `skip` param — it only supports cursor pagination
        (`cursor` / response `nextCursor`). Executions are returned newest-first
        by id, so `since` is applied client-side and paging stops once a page
        is entirely older than `since`, or n8n reports no further pages.
        """
        client = await self._get_client()

        cursor: str | None = None
        fetched = 0
        per_page = min(limit, 50)  # n8n max per page
        rate_limit_retries = 0
        max_rate_limit_retries = 3

        while fetched < limit:
            params: dict = {"limit": per_page, "includeData": "true"}
            if cursor:
                params["cursor"] = cursor

            try:
                response = await client.get("/api/v1/executions", params=params)
                response.raise_for_status()
                data = response.json()

                executions = data.get("data", [])
                if not executions:
                    break

                page_has_recent = False
                for exec_data in executions:
                    started_at_str = exec_data.get("startedAt")
                    if started_at_str:
                        started_at = datetime.fromisoformat(
                            started_at_str.replace("Z", "+00:00")
                        )
                        if started_at < since:
                            continue
                        page_has_recent = True
                    else:
                        page_has_recent = True

                    yield self._normalize_execution(exec_data)
                    fetched += 1
                    if fetched >= limit:
                        break

                cursor = data.get("nextCursor")
                if not cursor or not page_has_recent:
                    # No more pages, or this whole page was older than `since`
                    # (and since results are newest-first, all further pages
                    # would be older still).
                    break

                rate_limit_retries = 0
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.1)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limited - bounded wait and retry
                    rate_limit_retries += 1
                    if rate_limit_retries > max_rate_limit_retries:
                        raise ExternalServiceError(
                            message="n8n API rate limit exceeded after retries",
                            service="n8n",
                        ) from e
                    await asyncio.sleep(5)
                    continue
                raise ExternalServiceError(
                    message=f"n8n API error: {e.response.status_code}",
                    service="n8n",
                ) from e
            except Exception as e:
                logger.error("n8n.list_executions_failed", error=str(e))
                raise

    async def get_execution_detail(self, platform_run_id: str) -> NormalizedExecution:
        """Fetch full execution detail from n8n."""
        client = await self._get_client()

        try:
            response = await client.get(f"/api/v1/executions/{platform_run_id}")
            response.raise_for_status()
            data = response.json()
            return self._normalize_execution(data)
        except httpx.HTTPStatusError as e:
            raise ExternalServiceError(
                message=f"n8n execution fetch error: {e.response.status_code}",
                service="n8n",
            ) from e

    async def list_workflow_credentials(self) -> list[dict]:
        """List connected OAuth credentials in n8n."""
        client = await self._get_client()

        try:
            response = await client.get("/api/v1/credentials")
            response.raise_for_status()
            return list(response.json().get("data", []))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise IntegrationError(
                    message="API key invalid or expired",
                    platform="n8n",
                ) from e
            return []

    def normalize_status(self, platform_status: str) -> str:
        """Map n8n status values to normalized status."""
        status_map = {
            "success": "success",
            "finished": "success",
            "error": "error",
            "failed": "error",
            "running": "running",
            "waiting": "running",
            "canceled": "error",
            "crashed": "error",
        }
        return status_map.get(platform_status.lower(), "running")

    def _normalize_execution(self, exec_data: dict) -> NormalizedExecution:
        """Convert n8n execution response to NormalizedExecution."""
        # n8n reports terminal state two ways: a boolean `finished` and a
        # string `status`. Always prefer `status` and route it through the
        # mapping table — treating "anything finished that isn't 'error'" as
        # success silently records `crashed` and `canceled` runs as healthy,
        # which is exactly the class of failure this platform exists to catch.
        finished = exec_data.get("finished", False)
        raw_status = exec_data.get("status")

        if raw_status:
            status = self.normalize_status(str(raw_status))
        elif finished:
            status = "success"
        else:
            status = "running"

        # Extract error info
        error_msg = None
        error_node = None
        data = exec_data.get("data", {})
        if data and isinstance(data, dict):
            result_data = data.get("resultData", {})
            if result_data:
                error = result_data.get("error", {})
                if isinstance(error, dict):
                    error_msg = error.get("message")
                    error_node = error.get("name", "").replace("NodeApiError: ", "")

        # Parse timestamps
        started_at = None
        finished_at = None
        if exec_data.get("startedAt"):
            started_at = datetime.fromisoformat(exec_data["startedAt"].replace("Z", "+00:00"))
        if exec_data.get("stoppedAt"):
            finished_at = datetime.fromisoformat(exec_data["stoppedAt"].replace("Z", "+00:00"))

        # Calculate duration
        duration_ms = None
        if started_at and finished_at:
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        # Extract items processed from result data
        items_processed = None
        if data and isinstance(data, dict):
            result_data = data.get("resultData", {})
            if result_data:
                # n8n stores item count in various places
                for key in ["runData", "data"]:
                    run_data = result_data.get(key, {})
                    if isinstance(run_data, dict) and run_data:
                        total_items = sum(
                            len(node_data) if isinstance(node_data, list) else 0
                            for node_data in run_data.values()
                        )
                        if total_items > 0:
                            items_processed = total_items
                            break

        # Node count
        node_count = len(data.get("resultData", {}).get("runData", {})) if data else 0

        return NormalizedExecution(
            platform_run_id=exec_data.get("id", ""),
            workflow_id=exec_data.get("workflowId", ""),
            workflow_name=exec_data.get("workflowName"),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            node_count=node_count,
            items_processed=items_processed,
            error_message=error_msg,
            error_node=error_node,
            raw_payload=exec_data,
        )
