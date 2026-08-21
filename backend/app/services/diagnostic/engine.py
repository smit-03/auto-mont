"""
Multi-tier diagnostic engine.

Blueprint Part 7.2. Tiers run fast-to-expensive and the cascade stops at the
first tier confident enough to answer, so an execution explained by a static
rule never pays for a graph walk or an LLM call:

    1. Rule-based          <50ms   confidence >= 0.90
    2. Schema check        <100ms  confidence >= 0.85
    3. FKG traversal       <200ms  confidence >= 0.80
    4. Historical baseline <300ms  confidence >= 0.75   (not attached yet)
    5. LLM Judge           <5s                          (not attached yet)

Tiers 4-5 are deliberately absent rather than present as stubs returning None.
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

Every concluded diagnosis — from any tier, not only tier 3 — writes into the
Failure Knowledge Graph. This is `diagnose()`'s second side effect (the first
is tier 2 advancing the schema fingerprint): Blueprint Part 3 scope is "every
diagnosed failure creates nodes + edges," and this is the only place that
happens, so a tier 1 rule match feeds the graph exactly as a tier 3 match does.
Ingestion is idempotent — re-diagnosing a recurrence bumps occurrence_count
rather than duplicating nodes — which is also how the graph accumulates toward
the Phase 3 exit criterion ("50+ diagnosed failures").
"""

import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.diagnostic.fkg import ingest_failure, signature_hash
from app.services.diagnostic.fkg_traversal import (
    best_remediation,
    best_root_cause,
    find_root_causes,
)
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
        execution_id: uuid.UUID | None = None,
    ) -> TieredDiagnosis | None:
        """
        Walk the tiers and return the first answer at or above its threshold.

        Returns None when no attached tier can explain the execution. That is a
        real answer — "nothing known explains this" — and is what tier 5 exists
        to escalate once attached. Callers must not read None as "healthy".

        Not side-effect free, in two ways: tier 2 advances the stored schema
        fingerprint, and a concluded diagnosis is written into the Failure
        Knowledge Graph before it is returned. Diagnosing one execution twice
        therefore neither answers the same way twice nor is free to repeat.
        This belongs on the ingestion path, not on a read endpoint a page
        refresh can trigger.

        `execution_id` is optional and currently only reaches the graph, as
        FailureInstance.properties.execution_id — it is not part of any tier's
        own logic. None until the caller (eventually the polling path) has a
        persisted execution row to point at.
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
                await self._ingest_diagnosis(
                    session,
                    normalized,
                    result,
                    workspace_id=workspace_id,
                    platform=platform,
                    execution_id=execution_id,
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

    async def _ingest_diagnosis(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        result: TieredDiagnosis,
        *,
        workspace_id: uuid.UUID,
        platform: str,
        execution_id: uuid.UUID | None,
    ) -> None:
        """
        Write a concluded diagnosis into the graph, whichever tier produced it.

        `ingest_failure` keys everything on `error_message`; a schema-check
        diagnosis has none — drift is only detected on successful runs, which
        carry no error — so this falls back to the diagnosis's own root_cause
        text. That text is deterministic for a given drift shape (same fields,
        same wording), so it is just as valid a signature key as a real error
        message would be.
        """
        error_message = normalized.error_message or result.root_cause
        await ingest_failure(
            session,
            workspace_id=workspace_id,
            error_message=error_message,
            category=result.category,
            root_cause=result.root_cause,
            confidence=result.confidence,
            tier=result.tier_used.value,
            suggested_fix=result.suggested_fix,
            remediation_playbook=result.remediation_playbook,
            platform=platform,
            workflow_id=normalized.workflow_id,
            workflow_name=normalized.workflow_name,
            error_node=normalized.error_node,
            execution_id=execution_id,
            platform_run_id=normalized.platform_run_id,
        )

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

    async def _tier_fkg_traversal(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """
        Tier 3 — has this workspace's graph already explained this signature?

        Reads what `_ingest_diagnosis` writes: an execution's error message is
        hashed into the same signature key ingestion uses, so a failure this
        platform diagnosed before (by any tier) is recognised here even when
        tiers 1-2 have nothing to say about this particular occurrence.

        No error message means no signature to look up, and no query is worth
        running — an execution here is only ever a failure (tiers 1-2 already
        had their turn), so a missing message means the platform gave nothing
        to key on, not that the graph was empty.
        """
        if not normalized.error_message or not normalized.error_message.strip():
            return None

        sig_hash = signature_hash(normalized.error_message)
        matches = await find_root_causes(
            session, workspace_id=workspace_id, signature_hash=sig_hash
        )

        root = best_root_cause(matches)
        if root is None:
            return None

        remediation = best_remediation(matches)
        return TieredDiagnosis(
            root_cause=root.label,
            category=root.properties.get("category", "unknown"),
            confidence=root.confidence,
            suggested_fix=(
                root.properties.get("suggested_fix")
                or "No remediation recorded for this root cause yet."
            ),
            tier_used=DiagnosticTier.FKG_TRAVERSAL,
            remediation_playbook=(
                remediation.properties.get("playbook") if remediation else None
            ),
        )


# Cascade order. Tiers 4-5 attach here as they are built.
_TIERS = [
    (DiagnosticTier.RULE_BASED, DiagnosticEngine._tier_rule_based),
    (DiagnosticTier.SCHEMA_CHECK, DiagnosticEngine._tier_schema_check),
    (DiagnosticTier.FKG_TRAVERSAL, DiagnosticEngine._tier_fkg_traversal),
]
