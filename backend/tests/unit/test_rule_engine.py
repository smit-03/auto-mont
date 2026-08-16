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


# Phase 2 deliverable 5: the rule engine covers all seven taxonomy categories
# with a minimum of 15 rules.

# (error_message, expected_rule_id) for each signature added in Phase 2.
PHASE_2_SIGNATURES = [
    # Authentication
    ("AADSTS700016: invalid_client — application not found", "AUTH_004"),
    ("Request failed with status 403: insufficient scope", "AUTH_003"),
    ("Request failed with status code 401", "AUTH_002"),
    # API & network
    ("Gateway Timeout (504) from upstream model", "API_002"),
    ("503 Service Unavailable", "API_003"),
    ("connect ECONNREFUSED 127.0.0.1:5678", "API_004"),
    ("getaddrinfo ENOTFOUND api.example.com", "API_004"),
    ("socket hang up", "API_004"),
    # Data quality & schema
    ('null value in column "email" violates not-null constraint', "SCHEMA_001"),
    ('duplicate key value violates unique constraint "orders_pkey"', "SCHEMA_002"),
    ('invalid input syntax for type integer: "abc"', "SCHEMA_003"),
    ("Unexpected token < in JSON at position 0", "SCHEMA_003"),
    # AI & agentic
    ("This model's maximum context length is 200000 tokens", "AI_001"),
    ("context_length_exceeded", "AI_001"),
    ("no such tool: lookup_customer", "AI_002"),
    ("Agent response does not match schema", "AI_002"),
    # Workflow logic & orchestration
    ("Maximum call stack size exceeded", "LOGIC_001"),
    ("GraphRecursionError: recursion limit of 25 reached", "LOGIC_001"),
    ("TypeError: Cannot read property 'id' of undefined", "LOGIC_002"),
    ("AttributeError: 'NoneType' object has no attribute 'get'", "LOGIC_002"),
    ("Workflow timed out after 300s", "LOGIC_003"),
    ("Execution was aborted", "LOGIC_003"),
    # Infrastructure
    ("ENOSPC: no space left on device, write", "INFRA_003"),
]


class TestPhase2TaxonomyCoverage:
    """The expanded signature table: all 7 categories, 15+ rules."""

    @pytest.fixture
    def engine(self):
        return DiagnosticRuleEngine(SIGNATURE_RULES)

    @pytest.mark.parametrize(("error_message", "expected_rule_id"), PHASE_2_SIGNATURES)
    def test_signature_matches_expected_rule(self, engine, error_message, expected_rule_id):
        """Each Phase 2 signature is diagnosed by its own rule, not the fallback."""
        result = engine.diagnose({"status": "error", "error_message": error_message})

        assert result is not None, f"no rule matched {error_message!r}"
        assert result.rule_id == expected_rule_id

    def test_covers_all_seven_taxonomy_categories(self):
        """Phase 2 exit criteria: every failure class in the research taxonomy."""
        categories = {r["category"] for r in SIGNATURE_RULES}

        assert {"auth", "api", "logic", "schema", "infra", "ai", "silent"} <= categories

    def test_minimum_fifteen_rules(self):
        """Deliverable 5 specifies a minimum of 15 rules."""
        signature_rules = [r for r in SIGNATURE_RULES if r["id"] != "UNKNOWN_001"]

        assert len(signature_rules) >= 15

    def test_every_rule_carries_an_actionable_fix(self):
        """A diagnosis without a remediation is not worth alerting on."""
        for rule in SIGNATURE_RULES:
            assert rule["suggested_fix"].strip(), f"{rule['id']} has an empty suggested_fix"
            assert rule["root_cause"].strip(), f"{rule['id']} has an empty root_cause"
            assert 0.0 <= rule["confidence"] <= 1.0, f"{rule['id']} confidence out of range"

    def test_rule_ids_are_unique(self):
        """Duplicate ids would make dedup keys collide across distinct failures."""
        ids = [r["id"] for r in SIGNATURE_RULES]

        assert len(ids) == len(set(ids))


class TestRulePrecedence:
    """
    diagnose() returns the first match, so ordering is behaviour, not style.
    These pin the orderings that matter.
    """

    @pytest.fixture
    def engine(self):
        return DiagnosticRuleEngine(SIGNATURE_RULES)

    def test_invalid_grant_wins_over_bare_401(self, engine):
        """
        A 401 carrying invalid_grant is an expired refresh token, which is a
        far more actionable diagnosis than "authentication failed".
        """
        result = engine.diagnose(
            {"status": "error", "error_message": "401 Unauthorized: invalid_grant"}
        )

        assert result.rule_id == "AUTH_001"

    def test_403_wins_over_bare_401(self, engine):
        """
        Scope failures must not be reported as expiry failures — re-authenticating
        with the same scopes would not fix a 403, so the wrong fix wastes the
        user's time and erodes trust in the alert.
        """
        result = engine.diagnose(
            {"status": "error", "error_message": "403 Forbidden — unauthorized request"}
        )

        assert result.rule_id == "AUTH_003"

    def test_gateway_timeout_wins_over_generic_timeout(self, engine):
        """A 504 is an upstream problem, not a workflow-logic timeout."""
        result = engine.diagnose(
            {"status": "error", "error_message": "504 Gateway Timeout: execution timed out"}
        )

        assert result.rule_id == "API_002"


class TestStatusCodeBoundaries:
    """
    HTTP status codes match on word boundaries, not bare substrings.

    `contains "429"` also fires on 1429, 4290, and any id or timestamp
    containing those digits — which mis-categorises the failure and hands the
    user a confidently wrong fix.
    """

    @pytest.fixture
    def engine(self):
        return DiagnosticRuleEngine(SIGNATURE_RULES)

    @pytest.mark.parametrize(
        "error_message",
        [
            "Order 1429 could not be processed",
            "Item 4290 rejected by downstream",
            "Record 14012 missing required field",
            "Batch 5040 failed validation",
        ],
    )
    def test_embedded_digits_do_not_match_status_code_rules(self, engine, error_message):
        """Digits inside a larger number are not a status code."""
        result = engine.diagnose({"status": "error", "error_message": error_message})

        # Falls through to the fallback rather than claiming a specific cause.
        assert result.rule_id == "UNKNOWN_001"

    @pytest.mark.parametrize(
        ("error_message", "expected_rule_id"),
        [
            ("429 Too Many Requests", "API_001"),
            ("HTTP/1.1 429", "API_001"),
            ("status=504", "API_002"),
            ("[503] upstream", "API_003"),
        ],
    )
    def test_delimited_status_codes_still_match(self, engine, error_message, expected_rule_id):
        """Boundary matching must not become so strict it misses real codes."""
        result = engine.diagnose({"status": "error", "error_message": error_message})

        assert result.rule_id == expected_rule_id
