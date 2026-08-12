"""
Rule engine for static error signature matching.

Directly derived from the research taxonomy with Phase 1 rules:
- AUTH_001: invalid_grant (OAuth refresh token expired)
- API_001: 429 (rate limiting)
- SILENT_001: items_processed == 0 on success (silent operational failure)
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RuleMatch:
    """Result of a rule match."""

    rule_id: str
    category: Literal["auth", "api", "logic", "schema", "infra", "ai", "silent", "unknown"]
    root_cause: str
    confidence: float
    suggested_fix: str
    remediation_playbook: str | None
    requires_hitl: bool


# Phase 1 rules - minimum required rules
SIGNATURE_RULES: list[dict] = [
    # Category: Authentication
    {
        "id": "AUTH_001",
        "pattern": "invalid_grant",
        "field": "error_message",
        "match": "contains_lower",
        "category": "auth",
        "root_cause": "OAuth refresh token expired or revoked",
        "confidence": 0.97,
        "suggested_fix": (
            "The OAuth refresh token has been invalidated. This typically occurs when "
            "the GCP app is in 'Testing' mode (7-day token expiry), when the user changes "
            "their password, or when tokens are manually revoked. Re-authenticate the "
            "credential and consider migrating to a Google Service Account."
        ),
        "remediation_playbook": "oauth_refresh",
        "requires_hitl": False,
    },
    {
        "id": "AUTH_002",
        "pattern": "401",
        "field": "error_message",
        "match": "contains",
        "category": "auth",
        "root_cause": "API authentication failed — key invalid or revoked",
        "confidence": 0.90,
        "suggested_fix": "Verify the API key is still active and has not been rotated.",
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # Category: Rate Limiting
    {
        "id": "API_001",
        "pattern": "429",
        "field": "error_message",
        "match": "contains",
        "category": "api",
        "root_cause": "API rate limit exceeded — too many concurrent requests",
        "confidence": 0.95,
        "suggested_fix": (
            "Reduce parallel execution concurrency in your workflow. Add delay nodes "
            "between API calls in loops. Consider upgrading your API tier."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    # Category: Infrastructure
    {
        "id": "INFRA_001",
        "pattern": "out of memory",
        "field": "error_message",
        "match": "contains_lower",
        "category": "infra",
        "root_cause": "Container OOM kill — memory limit exceeded during execution",
        "confidence": 0.92,
        "suggested_fix": (
            "The execution consumed more RAM than the container limit. "
            "Split this workflow into smaller sub-workflows. Avoid loading "
            "large datasets into memory — use streaming or batched processing. "
            "Consider increasing the container memory allocation."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "INFRA_002",
        "pattern": "database is locked",
        "field": "error_message",
        "match": "contains_lower",
        "category": "infra",
        "root_cause": "SQLite write lock under concurrent access",
        "confidence": 0.95,
        "suggested_fix": "SQLite does not support concurrent writers. Migrate to Postgres for production workloads with concurrent executions.",
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # Category: Silent Failure
    {
        "id": "SILENT_001",
        "pattern": None,
        "field": "items_processed",
        "match": "equals_zero_on_success",
        "category": "silent",
        "root_cause": "Silent operational failure — execution succeeded but processed zero records",
        "confidence": 0.85,
        "suggested_fix": (
            "The workflow reported success but processed no items. This may indicate: "
            "an empty trigger (IMAP socket silently disconnected, webhook not receiving), "
            "upstream data source returned empty result, or a filter condition blocking all records."
        ),
        "remediation_playbook": "heartbeat_alert",
        "requires_hitl": True,
    },
    {
        "id": "UNKNOWN_001",
        "pattern": None,
        "field": "error_message",
        "match": "fallback_failed_execution",
        "category": "unknown",
        "root_cause": "Unknown execution failure — raw error captured for investigation",
        "confidence": 0.35,
        "suggested_fix": (
            "Review the raw error message and workflow logs to determine the root cause. "
            "If the failure is unexpected, inspect the node execution details and recent changes."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
]


class DiagnosticRuleEngine:
    """
    Fast-path rule-based diagnostic engine.

    Matches error signatures against known failure patterns.
    Target: <50ms per diagnosis.
    """

    def __init__(self, rules: list[dict] | None = None):
        self.rules = rules or SIGNATURE_RULES
        # Pre-compile regex patterns for faster matching
        self._compiled_rules: list[dict] = []
        self._fallback_rule: dict | None = None
        # Looked up by match type, not by list position: the silent-failure
        # check runs before the signature loop, and a positional lookup would
        # silently select the wrong rule as soon as a rule is appended.
        self._silent_rule: dict | None = None
        for rule in self.rules:
            compiled = rule.copy()
            if rule["match"] in ("contains", "contains_lower") and rule["pattern"]:
                compiled["_pattern_re"] = re.compile(
                    re.escape(rule["pattern"]),
                    re.IGNORECASE if rule["match"] == "contains_lower" else 0,
                )
            if rule["id"] == "UNKNOWN_001":
                self._fallback_rule = compiled
            else:
                if rule["match"] == "equals_zero_on_success":
                    self._silent_rule = compiled
                self._compiled_rules.append(compiled)

        if self._fallback_rule is None:
            self._fallback_rule = {
                "id": "UNKNOWN_001",
                "category": "unknown",
                "root_cause": "Unknown execution failure — raw error captured for investigation",
                "confidence": 0.35,
                "suggested_fix": (
                    "Review the raw error message and workflow logs to determine the root cause. "
                    "If the failure is unexpected, inspect the node execution details and recent changes."
                ),
                "remediation_playbook": None,
                "requires_hitl": True,
            }

    def diagnose(self, execution: dict) -> RuleMatch | None:
        """
        Diagnose an execution using static rules.

        Returns the first matching rule or a fallback UNKNOWN alert for failed
        executions when no signature matches.
        """
        # Check for silent failure (items_processed == 0 with success status)
        if (
            self._silent_rule is not None
            and execution.get("items_processed") == 0
            and execution.get("status") == "success"
        ):
            return self._apply_rule(self._silent_rule, execution)

        error_message = execution.get("error_message", "") or ""
        status = str(execution.get("status") or "").strip().lower()

        for rule in self._compiled_rules:
            if rule["match"] == "equals_zero_on_success":
                continue  # Already checked above

            match = self._match_rule(rule, error_message, execution)
            if match:
                logger.debug(
                    "rule.match_found",
                    rule_id=rule["id"],
                    category=rule["category"],
                    execution_id=execution.get("id"),
                )
                return self._apply_rule(rule, execution)

        if status in {"error", "failed"} and self._fallback_rule is not None:
            logger.info(
                "rule.fallback_used",
                execution_id=execution.get("id"),
                status=status,
                error_message=error_message,
            )
            return self._apply_rule(self._fallback_rule, execution)

        return None

    def _match_rule(self, rule: dict, error_message: str, execution: dict) -> bool:
        """Check if a rule matches the execution."""
        match_type = rule["match"]
        pattern = rule["pattern"]

        if match_type == "contains":
            return pattern in error_message
        elif match_type == "contains_lower":
            return pattern.lower() in error_message.lower()

        return False

    def _apply_rule(self, rule: dict, execution: dict) -> RuleMatch:
        """Apply a matched rule to produce a RuleMatch."""
        return RuleMatch(
            rule_id=rule["id"],
            category=rule["category"],
            root_cause=rule["root_cause"],
            confidence=rule["confidence"],
            suggested_fix=rule["suggested_fix"],
            remediation_playbook=rule["remediation_playbook"],
            requires_hitl=rule["requires_hitl"],
        )


def get_rule_engine() -> DiagnosticRuleEngine:
    """Get the global rule engine instance."""
    return DiagnosticRuleEngine(SIGNATURE_RULES)
