"""
Rule engine for static error signature matching.

Directly derived from the research taxonomy. Phase 2 expands the table to cover
all seven failure classes: auth, api, logic, schema, infra, ai, silent.

Rule order is significant — ``diagnose()`` returns the first match, so specific
signatures must precede general ones. ``invalid_grant`` sits above the bare 401
rule because an expired refresh token is a more actionable diagnosis than
"authentication failed", and both can appear in the same message.

HTTP status codes match via ``regex`` with word boundaries rather than a bare
substring test. ``contains "429"`` also fires on ``1429``, ``4290`` and any
timestamp or id that happens to contain those digits, which on this table means
mis-categorising a failure and handing the user a confidently wrong fix.
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


SIGNATURE_RULES: list[dict] = [
    # =========================================================================
    # Category: Authentication (research: 35% of all integration failures)
    # =========================================================================
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
        "id": "AUTH_004",
        "pattern": r"invalid_client|unauthorized_client|invalid_?token",
        "field": "error_message",
        "match": "regex",
        "category": "auth",
        "root_cause": "OAuth client rejected — client credentials invalid or app deauthorized",
        "confidence": 0.93,
        "suggested_fix": (
            "The OAuth application itself was rejected, not just the user's token. "
            "Check that the client ID and secret in your credential are correct and "
            "have not been rotated, and that the app has not been deleted or "
            "deauthorized in the provider's console. Re-create the credential after "
            "confirming the app registration is active."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "AUTH_003",
        "pattern": r"\b403\b|forbidden|insufficient (?:scope|permission)|missing (?:scope|permission)",
        "field": "error_message",
        "match": "regex",
        "category": "auth",
        "root_cause": "Authorization failed — credential authenticated but lacks required scope",
        "confidence": 0.91,
        "suggested_fix": (
            "The credential is valid but is not permitted to perform this operation. "
            "This is a scope problem, not an expiry problem: re-authenticating with the "
            "same scopes will not fix it. Review the scopes granted when the credential "
            "was authorized, add the scope this node requires, and re-consent. For "
            "service accounts, check the IAM role bindings on the target resource."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "AUTH_002",
        "pattern": r"\b401\b|unauthorized|authentication failed",
        "field": "error_message",
        "match": "regex",
        "category": "auth",
        "root_cause": "API authentication failed — key invalid or revoked",
        "confidence": 0.90,
        "suggested_fix": "Verify the API key is still active and has not been rotated.",
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # =========================================================================
    # Category: API & Network Gateway (research: 429/504 dominant)
    # =========================================================================
    {
        "id": "API_001",
        "pattern": r"\b429\b|too many requests|rate limit",
        "field": "error_message",
        "match": "regex",
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
    {
        "id": "API_002",
        "pattern": r"\b504\b|gateway timeout",
        "field": "error_message",
        "match": "regex",
        "category": "api",
        "root_cause": "Gateway timeout — upstream LLM or API exceeded timeout window",
        "confidence": 0.90,
        "suggested_fix": (
            "The upstream API took too long to respond. For LLMs, reduce context length "
            "or switch to a faster model variant. Increase the node timeout setting "
            "or implement streaming responses."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    {
        "id": "API_003",
        "pattern": r"\b50[23]\b|service unavailable|bad gateway",
        "field": "error_message",
        "match": "regex",
        "category": "api",
        "root_cause": "Upstream service unavailable — provider outage or deploy in progress",
        "confidence": 0.88,
        "suggested_fix": (
            "The upstream provider returned a server-side error, so there is most "
            "likely nothing wrong with your workflow. Check the provider's status page. "
            "Add retry with exponential backoff to this node so a brief outage does not "
            "fail the whole run."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    {
        "id": "API_004",
        "pattern": r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|socket hang up",
        "field": "error_message",
        "match": "regex",
        "category": "api",
        "root_cause": "Network-level failure — host unreachable, DNS failed, or connection dropped",
        "confidence": 0.92,
        "suggested_fix": (
            "The request never completed a round trip, so this is a connectivity problem "
            "rather than an API error. Verify the hostname resolves and the port is "
            "reachable from the workflow host. For self-hosted services, check the "
            "container is running and on the same network. ENOTFOUND and EAI_AGAIN point "
            "at DNS specifically; ECONNRESET and socket hang up usually mean the peer "
            "closed the connection mid-request."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    # =========================================================================
    # Category: Data Quality & Schema
    # =========================================================================
    {
        "id": "SCHEMA_001",
        "pattern": r"violates not-null constraint|null value in column",
        "field": "error_message",
        "match": "regex",
        "category": "schema",
        "root_cause": "Database insert failed — required field is null",
        "confidence": 0.95,
        "suggested_fix": (
            "A field required by your database schema is arriving as null. "
            "Add a defensive null-check node before the database insert node. "
            "Inspect the upstream data source for missing or renamed fields."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "SCHEMA_002",
        "pattern": r"duplicate key value|violates unique constraint|already exists",
        "field": "error_message",
        "match": "regex",
        "category": "schema",
        "root_cause": "Uniqueness violation — the workflow re-inserted a record it already wrote",
        "confidence": 0.93,
        "suggested_fix": (
            "The workflow tried to write a row that already exists. This usually means "
            "the run is a retry or a duplicate trigger fired, and the insert is not "
            "idempotent. Switch the insert to an upsert keyed on the natural id, or add "
            "a lookup node that skips records already present."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "SCHEMA_003",
        "pattern": (
            r"invalid input syntax|invalid type|JSON at position|Unexpected token"
            r"|is not valid JSON|cannot be coerced"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "schema",
        "root_cause": "Type or parse failure — upstream payload shape does not match what the node expects",
        "confidence": 0.90,
        "suggested_fix": (
            "A value arrived in a type the receiving node could not accept, or a response "
            "that should have been JSON was not parseable. Inspect the raw payload on this "
            "execution to see what actually arrived — a provider returning an HTML error "
            "page with a 200 status is a common cause. Add explicit type coercion before "
            "this node."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # =========================================================================
    # Category: AI & Agentic Execution
    # =========================================================================
    {
        "id": "AI_001",
        "pattern": (
            r"context length|context_length_exceeded|maximum context"
            r"|too many tokens|prompt is too long"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "ai",
        "root_cause": "Context window exceeded — prompt plus history grew past the model's limit",
        "confidence": 0.94,
        "suggested_fix": (
            "The request exceeded the model's context window. In a loop or multi-turn "
            "agent this usually means conversation history is accumulating without being "
            "trimmed. Truncate or summarise history between turns, reduce the number of "
            "retrieved documents, or move to a model with a larger context window."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "AI_002",
        "pattern": (
            r"tool_use_failed|no such tool|unknown tool|invalid tool"
            r"|does not match schema|failed to parse.*(?:output|response)"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "ai",
        "root_cause": "Agent output contract violated — invalid tool call or malformed structured output",
        "confidence": 0.89,
        "suggested_fix": (
            "The model returned something the orchestrator could not execute: a call to a "
            "tool that is not registered, or output that failed schema validation. Confirm "
            "the tool names in the system prompt exactly match the registered tool "
            "definitions. Where the provider supports it, use constrained/structured "
            "output rather than parsing free text, and keep a validation retry on the "
            "parse step."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # =========================================================================
    # Category: Workflow Logic & Orchestration
    # =========================================================================
    {
        "id": "LOGIC_001",
        "pattern": (
            r"maximum (?:call stack|recursion|iterations)|too many iterations"
            r"|infinite loop|recursion limit|GraphRecursionError"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "logic",
        "root_cause": "Runaway loop — the workflow did not reach a terminating condition",
        "confidence": 0.92,
        "suggested_fix": (
            "The workflow looped until it hit a hard limit rather than exiting on its own. "
            "Check the loop's exit condition actually becomes true for the data being "
            "processed — an agent that keeps re-calling the same tool because it never "
            "recognises success is the usual cause. Add an explicit maximum-iteration "
            "guard that fails loudly instead of spinning."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "LOGIC_002",
        "pattern": (
            r"Cannot read propert(?:y|ies)|of undefined|of null"
            r"|is not a function|NoneType' object has no attribute"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "logic",
        "root_cause": "Expression referenced a field that was absent from the incoming item",
        "confidence": 0.88,
        "suggested_fix": (
            "An expression or code node read a property that did not exist on the item it "
            "received. The upstream data is shaped differently than the expression assumes "
            "— often because an optional field was omitted for this particular record. "
            "Inspect the raw payload for the failing item and guard the access with a "
            "default value."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "LOGIC_003",
        "pattern": (
            r"execution (?:was )?(?:aborted|cancell?ed|timed out)"
            r"|workflow timed out|exceeded maximum execution time"
        ),
        "field": "error_message",
        "match": "regex",
        "category": "logic",
        "root_cause": "Execution terminated early — aborted or exceeded the workflow time limit",
        "confidence": 0.87,
        "suggested_fix": (
            "The run did not finish on its own. If it timed out, the workflow is doing more "
            "work per execution than its time limit allows — split it into batched "
            "sub-workflows or raise the timeout. If it was aborted, check whether the host "
            "restarted or an operator cancelled it. Note that any records processed before "
            "termination were still written, so partial writes are likely."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # =========================================================================
    # Category: Infrastructure & Resource
    # =========================================================================
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
    {
        "id": "INFRA_003",
        "pattern": r"ENOSPC|no space left on device|disk (?:is )?full|quota exceeded",
        "field": "error_message",
        "match": "regex",
        "category": "infra",
        "root_cause": "Storage exhausted — the host ran out of disk or hit a storage quota",
        "confidence": 0.94,
        "suggested_fix": (
            "The execution host has no writable space left. On self-hosted deployments the "
            "usual culprit is the execution history table or log volume growing unbounded — "
            "prune old execution data and set a retention policy. Check container volume "
            "usage before re-running, since every workflow on this host is affected, not "
            "just this one."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # =========================================================================
    # Category: Silent Operational Failure (the MVP wedge)
    # =========================================================================
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
            if rule["match"] in ("contains", "contains_lower", "regex") and rule["pattern"]:
                flags: int
                if rule["match"] == "regex":
                    source, flags = rule["pattern"], re.IGNORECASE
                else:
                    source = re.escape(rule["pattern"])
                    flags = re.IGNORECASE if rule["match"] == "contains_lower" else 0
                compiled["_pattern_re"] = re.compile(source, flags)
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
        """
        Check if a rule matches the execution.

        Dispatches through the pattern compiled in __init__ for all three text
        match types. This previously re-did the substring test by hand and
        ignored ``_pattern_re`` entirely, so every compiled pattern was dead
        work — and there was no way to express a rule that needed more than a
        substring, which the HTTP status-code rules do.
        """
        if rule["match"] not in ("contains", "contains_lower", "regex"):
            return False

        compiled = rule.get("_pattern_re")
        if compiled is None:
            return False

        return compiled.search(error_message) is not None

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
