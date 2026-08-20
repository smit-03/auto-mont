"""
Multi-tier diagnostic engine.

Blueprint Part 7.2. Tiers run fast-to-expensive and the cascade stops at the
first tier confident enough to answer, so an execution explained by a static
rule never pays for a graph walk or an LLM call:

    1. Rule-based          <50ms   confidence >= 0.90
    2. Schema check        <100ms  confidence >= 0.85
    3. FKG traversal       <200ms  confidence >= 0.80   (not attached yet)
    4. Historical baseline <300ms  confidence >= 0.75   (not attached yet)
    5. LLM Judge           <5s                          (not attached yet)

Tiers 3-5 are deliberately absent rather than present as stubs returning None.
A stub in a dispatch position is the shape just retired from HeartbeatEngine
(commit 7d5402d): it reads as wired, answers nothing, and nothing fails loudly
enough to notice. `_TIERS` below is the attach point; adding a tier means adding
it there.

This engine is not the alerting path. `_diagnose_and_alert` in
`worker/tasks/polling.py` unions every detector's findings because one execution
can be worth several alerts at once — a drift *and* an assertion violation are
two separate things a customer needs told. Diagnosis answers a different
question: what single thing best explains this failure. The two coexist, and
this engine does not replace that union.
"""

import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.diagnostic.rule_engine import DiagnosticRuleEngine
from app.services.diagnostic.schema_checker import build_drift_fix, check_schema_drift
from app.services.sentinel.base_adapter import NormalizedExecution

logger = get_logger(__name__)


class DiagnosticTier(Enum):
    RULE_BASED = "rule_based"
    SCHEMA_CHECK = "schema_check"
    FKG_TRAVERSAL = "fkg_traversal"
    HISTORICAL_BASELINE = "historical_baseline"
    LLM_JUDGE = "llm_judge"


# Per-tier confidence floor, from Blueprint Part 7.2. A tier answering below its
# floor does not stop the cascade — the next tier gets a turn.
TIER_THRESHOLDS: dict[DiagnosticTier, float] = {
    DiagnosticTier.RULE_BASED: 0.90,
    DiagnosticTier.SCHEMA_CHECK: 0.85,
    DiagnosticTier.FKG_TRAVERSAL: 0.80,
    DiagnosticTier.HISTORICAL_BASELINE: 0.75,
}

# A fingerprint diff is an observation, not an inference: the shape either
# changed or it did not. Confidence here therefore encodes *consequence*, not
# certainty. Removals and type changes break downstream consumers and are the
# explanation for a failure; a purely additive drift is almost always harmless —
# the same reasoning behind SchemaDrift.severity — so it scores below the tier
# floor and lets deeper tiers look for a better answer instead of declaring the
# case closed.
_DRIFT_CONFIDENCE_BREAKING = 0.95
_DRIFT_CONFIDENCE_ADDITIVE = 0.50


@dataclass
class TieredDiagnosis:
    """
    One tier's answer to "what explains this execution".

    Named TieredDiagnosis rather than DiagnosticResult because that name is
    already published by `app.schemas.execution.DiagnosticResult`, the response
    model of GET /executions/{id}/diagnostic. The API keeps the name it already
    serves; this is the internal type.
    """

    root_cause: str
    category: str
    confidence: float
    suggested_fix: str
    tier_used: DiagnosticTier
    remediation_playbook: str | None = None
    requires_hitl: bool = False


class DiagnosticEngine:
    """Runs the tier cascade and returns the first sufficiently confident answer."""

    def __init__(self, rule_engine: DiagnosticRuleEngine | None = None):
        # Rules are immutable once compiled, so one engine is reusable. Injectable
        # so a caller holding a custom rule table does not get a second copy of
        # the default one built underneath it.
        self._rule_engine = rule_engine or DiagnosticRuleEngine()

    async def diagnose(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """
        Walk the tiers and return the first answer at or above its threshold.

        Returns None when no attached tier can explain the execution. That is a
        real answer — "nothing known explains this" — and is what tier 5 exists
        to escalate once attached. Callers must not read None as "healthy".

        Not side-effect free: tier 2 advances the stored schema fingerprint, so
        diagnosing one execution twice does not answer the same way twice. This
        belongs on the ingestion path, not on a read endpoint a page refresh can
        trigger.
        """
        for tier, run in _TIERS:
            result = await run(
                self,
                session,
                normalized,
                workspace_id=workspace_id,
                integration_id=integration_id,
                platform=platform,
            )
            if result is None:
                continue

            threshold = TIER_THRESHOLDS[tier]
            if result.confidence >= threshold:
                logger.info(
                    "diagnostic.tier_answered",
                    tier=tier.value,
                    confidence=result.confidence,
                    category=result.category,
                    workflow_id=normalized.workflow_id,
                )
                return result

            logger.debug(
                "diagnostic.tier_below_threshold",
                tier=tier.value,
                confidence=result.confidence,
                threshold=threshold,
                workflow_id=normalized.workflow_id,
            )

        return None

    async def _tier_rule_based(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """Tier 1 — static error signatures. No database access."""
        match = self._rule_engine.diagnose(
            {
                "error_message": normalized.error_message,
                "status": normalized.status,
                "items_processed": normalized.items_processed,
            }
        )
        if match is None:
            return None

        return TieredDiagnosis(
            root_cause=match.root_cause,
            category=match.category,
            confidence=match.confidence,
            suggested_fix=match.suggested_fix,
            tier_used=DiagnosticTier.RULE_BASED,
            remediation_playbook=match.remediation_playbook,
            requires_hitl=match.requires_hitl,
        )

    async def _tier_schema_check(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """Tier 2 — output shape against the stored fingerprint."""
        drift = await check_schema_drift(
            session,
            workspace_id=workspace_id,
            integration_id=integration_id,
            platform=platform,
            workflow_id=normalized.workflow_id,
            platform_run_id=normalized.platform_run_id,
            status=normalized.status,
            raw_payload=normalized.raw_payload,
        )
        if drift is None:
            return None

        breaking = bool(drift.removed or drift.type_changed)
        return TieredDiagnosis(
            root_cause=f"Output schema changed for {drift.workflow_id}: {drift.describe()}",
            category="schema",
            confidence=_DRIFT_CONFIDENCE_BREAKING if breaking else _DRIFT_CONFIDENCE_ADDITIVE,
            suggested_fix=build_drift_fix(drift),
            tier_used=DiagnosticTier.SCHEMA_CHECK,
        )


# Cascade order. Tiers 3-5 attach here as they are built.
_TIERS = [
    (DiagnosticTier.RULE_BASED, DiagnosticEngine._tier_rule_based),
    (DiagnosticTier.SCHEMA_CHECK, DiagnosticEngine._tier_schema_check),
]
