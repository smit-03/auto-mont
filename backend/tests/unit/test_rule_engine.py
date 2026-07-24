"""
Unit tests for Diagnostic Rule Engine - Task 34.

Tests all Phase 1 rules: AUTH_001, API_001, SILENT_001.
"""

import pytest

from app.services.diagnostic.rule_engine import (
    SIGNATURE_RULES,
    DiagnosticRuleEngine,
    get_rule_engine,
)


class TestDiagnosticRuleEngine:
    """Tests for the rule-based diagnostic engine."""

    @pytest.fixture
    def engine(self):
        return DiagnosticRuleEngine(SIGNATURE_RULES)

    # AUTH_001: invalid_grant
    def test_auth_001_detects_invalid_grant(self, engine, mock_execution_invalid_grant):
        """AUTH_001 should detect invalid_grant errors."""
        result = engine.diagnose(mock_execution_invalid_grant)

        assert result is not None
        assert result.rule_id == "AUTH_001"
        assert result.category == "auth"
        assert result.confidence == 0.97
        assert "OAuth" in result.root_cause
        assert result.remediation_playbook == "oauth_refresh"
        assert result.requires_hitl is False

    def test_auth_001_case_insensitive(self, engine):
        """AUTH_001 should match case-insensitively."""
        exec_data = {"error_message": "INVALID_GRANT: token expired"}
        result = engine.diagnose(exec_data)

        assert result is not None
        assert result.rule_id == "AUTH_001"

    # API_001: 429 rate limiting
    def test_api_001_detects_rate_limit(self, engine, mock_execution_429):
        """API_001 should detect 429 rate limit errors."""
        result = engine.diagnose(mock_execution_429)

        assert result is not None
        assert result.rule_id == "API_001"
        assert result.category == "api"
        assert result.confidence == 0.95
        assert "rate limit" in result.root_cause.lower()
        assert result.remediation_playbook == "retry_backoff"
        assert result.requires_hitl is False

    def test_api_001_suggest_reduce_concurrency(self, engine, mock_execution_429):
        """API_001 should suggest reducing concurrency."""
        result = engine.diagnose(mock_execution_429)

        assert (
            "concurrency" in result.suggested_fix.lower()
            or "parallel" in result.suggested_fix.lower()
        )

    # SILENT_001: zero items on success
    def test_silent_001_detects_zero_items(self, engine, mock_execution_silent_fail):
        """SILENT_001 should detect zero items processed on success."""
        result = engine.diagnose(mock_execution_silent_fail)

        assert result is not None
        assert result.rule_id == "SILENT_001"
        assert result.category == "silent"
        assert result.confidence == 0.85
        assert "silent" in result.root_cause.lower()
        assert "zero" in result.root_cause.lower()
        assert result.requires_hitl is True

    def test_silent_001_only_on_success(self, engine):
        """SILENT_001 should only trigger on status=success, not error."""
        exec_data = {
            "status": "error",
            "items_processed": 0,
            "error_message": "some error",
        }
        result = engine.diagnose(exec_data)

        # Should not trigger SILENT_001 because status is error
        if result:
            assert result.rule_id != "SILENT_001"

    def test_silent_001_with_items_processed(self, engine):
        """SILENT_001 should not trigger when items were processed."""
        exec_data = {
            "status": "success",
            "items_processed": 5,
        }
        result = engine.diagnose(exec_data)

        assert result is None

    # INFRA_001: Out of memory
    def test_infra_001_detects_oom(self, engine, mock_execution_oom):
        """INFRA_001 should detect OOM errors."""
        result = engine.diagnose(mock_execution_oom)

        assert result is not None
        assert result.rule_id == "INFRA_001"
        assert result.category == "infra"
        assert result.confidence == 0.92
        assert "OOM" in result.root_cause or "memory" in result.root_cause.lower()

    # No match cases
    def test_no_match_returns_none(self, engine):
        """Should return None when no rules match."""
        exec_data = {
            "status": "success",
            "items_processed": 100,
            "error_message": None,
        }
        result = engine.diagnose(exec_data)

        assert result is None

    def test_unknown_error_returns_fallback_rule_for_failed_execution(self, engine):
        """Failed executions without a signature match should return an UNKNOWN fallback rule."""
        exec_data = {
            "status": "error",
            "items_processed": 100,
            "error_message": "some random error that doesn't match any rule",
        }
        result = engine.diagnose(exec_data)

        assert result is not None
        assert result.rule_id == "UNKNOWN_001"
        assert result.category == "unknown"
        assert result.confidence == 0.35
        assert "raw error" in result.root_cause.lower()
        assert result.suggested_fix

    # Rule count verification
    def test_rule_count_minimum(self):
        """Should have at least 3 Phase 1 rules."""
        # Phase 1 requires AUTH_001, API_001, SILENT_001 minimum
        rule_ids = {r["id"] for r in SIGNATURE_RULES}
        assert "AUTH_001" in rule_ids
        assert "API_001" in rule_ids
        assert "SILENT_001" in rule_ids

    def test_all_rules_have_required_fields(self):
        """All rules should have required fields."""
        required = ["id", "category", "root_cause", "confidence", "suggested_fix", "requires_hitl"]

        for rule in SIGNATURE_RULES:
            for field in required:
                assert field in rule, f"Rule {rule.get('id', 'UNKNOWN')} missing field {field}"


class TestRuleEngineSingleton:
    """Test the singleton getter function."""

    def test_get_rule_engine_returns_instance(self):
        engine = get_rule_engine()
        assert isinstance(engine, DiagnosticRuleEngine)
