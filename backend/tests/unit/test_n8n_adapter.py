"""
Unit tests for the n8n adapter's execution normalization.

Focused on the payload-shape assumptions that are easy to get wrong because
n8n reports the same fact in more than one place.
"""

import pytest

from app.services.sentinel.n8n_adapter import N8NAdapter


@pytest.fixture
def adapter():
    return N8NAdapter(base_url="http://n8n.test", api_key="k")


class TestWorkflowName:
    """
    n8n has no top-level `workflowName` on executions — the name is at
    `workflowData.name`, included because the poller polls with
    includeData=true. Reading only the top-level key left every execution
    nameless, and alert titles degraded to the raw workflow id.
    """

    def test_reads_name_from_workflow_data(self, adapter):
        payload = {
            "id": "8",
            "workflowId": "k5nqWU1s8Xpjjwkw",
            "status": "error",
            "workflowData": {"id": "k5nqWU1s8Xpjjwkw", "name": "Test - invalid_grant Failure"},
        }

        result = adapter._normalize_execution(payload)

        assert result.workflow_name == "Test - invalid_grant Failure"

    def test_prefers_top_level_key_when_present(self, adapter):
        """Kept so a future API version adding the field takes precedence."""
        payload = {
            "id": "9",
            "workflowId": "w",
            "status": "success",
            "workflowName": "Top Level",
            "workflowData": {"name": "Nested"},
        }

        result = adapter._normalize_execution(payload)

        assert result.workflow_name == "Top Level"

    @pytest.mark.parametrize(
        "payload",
        [
            {"id": "9", "workflowId": "w", "status": "success"},
            {"id": "9", "workflowId": "w", "status": "success", "workflowData": None},
            {"id": "9", "workflowId": "w", "status": "success", "workflowData": "unexpected"},
            {"id": "9", "workflowId": "w", "status": "success", "workflowData": {}},
        ],
    )
    def test_missing_or_malformed_workflow_data_yields_none(self, adapter, payload):
        """
        Detail fetches do not pass includeData, so workflowData is legitimately
        absent. None is valid and callers already fall back to the workflow id.
        """
        result = adapter._normalize_execution(payload)

        assert result.workflow_name is None


class TestStatusNormalization:
    """`status` is authoritative over the `finished` boolean."""

    @pytest.mark.parametrize(
        ("raw_status", "expected"),
        [
            ("success", "success"),
            ("error", "error"),
            ("crashed", "error"),
            ("canceled", "error"),
            ("running", "running"),
            ("waiting", "running"),
        ],
    )
    def test_maps_platform_status(self, adapter, raw_status, expected):
        payload = {"id": "1", "workflowId": "w", "status": raw_status, "finished": True}

        assert adapter._normalize_execution(payload).status == expected

    def test_crashed_run_is_not_recorded_as_success(self, adapter):
        """
        `finished: true` with `status: crashed` is a failure. Treating "finished
        and not error" as success is exactly the silent failure this platform
        exists to catch.
        """
        payload = {"id": "1", "workflowId": "w", "status": "crashed", "finished": True}

        assert adapter._normalize_execution(payload).status == "error"

    def test_falls_back_to_finished_flag_when_status_absent(self, adapter):
        assert (
            adapter._normalize_execution({"id": "1", "workflowId": "w", "finished": True}).status
            == "success"
        )
        assert (
            adapter._normalize_execution({"id": "1", "workflowId": "w", "finished": False}).status
            == "running"
        )
