"""
Test configuration and fixtures for WRP backend.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

# Set test environment variables before importing app
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/wrp_test"
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
os.environ["CREDENTIAL_MASTER_KEY"] = "0" * 64

from app.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def app():
    """Create test FastAPI app."""
    return create_app()


@pytest.fixture
async def client(app):
    """Create httpx test client."""
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c


# Model fixtures
@pytest.fixture
def mock_execution_invalid_grant():
    """Mock execution with invalid_grant error (AUTH_001)."""
    return {
        "id": "exec_test_123",
        "platform_run_id": "123",
        "workflow_id": "wf_auth",
        "workflow_name": "Auth Test Workflow",
        "status": "error",
        "started_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        "duration_ms": 1000,
        "node_count": 5,
        "items_processed": None,
        "error_message": "invalid_grant: Token expired or revoked",
        "error_node": "Google Sheets",
        "raw_payload": {
            "error": {"message": "invalid_grant", "name": "NodeApiError"},
            "data": {},
        },
    }


@pytest.fixture
def mock_execution_429():
    """Mock execution with 429 rate limit error (API_001)."""
    return {
        "id": "exec_test_456",
        "platform_run_id": "456",
        "workflow_id": "wf_api",
        "workflow_name": "API Test Workflow",
        "status": "error",
        "started_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        "duration_ms": 2000,
        "node_count": 3,
        "items_processed": None,
        "error_message": "HTTP 429: Too Many Requests",
        "error_node": "HTTP Request",
        "raw_payload": {
            "error": {"message": "429", "name": "HTTPError"},
            "data": {},
        },
    }


@pytest.fixture
def mock_execution_silent_fail():
    """Mock execution with success status but zero items (SILENT_001)."""
    return {
        "id": "exec_test_789",
        "platform_run_id": "789",
        "workflow_id": "wf_silent",
        "workflow_name": "Silent Fail Test Workflow",
        "status": "success",
        "started_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        "duration_ms": 3000,
        "node_count": 10,
        "items_processed": 0,  # Zero items on success = silent failure
        "error_message": None,
        "error_node": None,
        "raw_payload": {
            "data": {"resultData": {"runData": {}}},
        },
    }


@pytest.fixture
def mock_execution_oom():
    """Mock execution with OOM error (INFRA_001)."""
    return {
        "id": "exec_test_oom",
        "platform_run_id": "oom123",
        "workflow_id": "wf_oom",
        "workflow_name": "OOM Test Workflow",
        "status": "error",
        "started_at": datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        "duration_ms": 5000,
        "node_count": 20,
        "items_processed": 1000,
        "error_message": "Container terminated unexpectedly: Out of memory",
        "error_node": "Data Loader",
        "raw_payload": {},
    }


@pytest.fixture
def mock_credential_expiring():
    """Mock credential expiring in 24 hours."""

    class MockCredential:
        id = "cred_123"
        provider = "google"
        display_name = "Google OAuth"
        expires_at = datetime.now(UTC) + timedelta(hours=24)
        gcp_status = "testing"
        status = "active"

    return MockCredential()


@pytest.fixture
def mock_credential_expired():
    """Mock credential that already expired."""

    class MockCredential:
        id = "cred_456"
        provider = "google"
        display_name = "Expired Google OAuth"
        expires_at = datetime.now(UTC) - timedelta(hours=2)
        gcp_status = "production"
        status = "expired"

    return MockCredential()


@pytest.fixture
def mock_monitor_healthy():
    """Mock healthy heartbeat monitor."""

    class MockMonitor:
        id = "mon_123"
        workspace_id = "ws_123"
        name = "Test Heartbeat"
        monitor_type = "heartbeat"
        last_ping_at = datetime.now(UTC) - timedelta(minutes=1)
        grace_period_s = 300
        last_status = "healthy"

    return MockMonitor()


@pytest.fixture
def mock_monitor_expired():
    """Mock monitor that missed its heartbeat."""

    class MockMonitor:
        id = "mon_456"
        workspace_id = "ws_123"
        name = "Missed Heartbeat"
        monitor_type = "heartbeat"
        last_ping_at = datetime.now(UTC) - timedelta(minutes=10)
        grace_period_s = 300
        last_status = "failing"

    return MockMonitor()
