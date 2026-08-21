"""
Multi-tier diagnostic engine.

Blueprint Part 7.2. Tiers run fast-to-expensive and the cascade stops at the
first tier confident enough to answer, so an execution explained by a static
rule never pays for a graph walk or an LLM call:

    1. Rule-based          <50ms   confidence >= 0.90
    2. Schema check        <100ms  confidence >= 0.85
    3. FKG traversal       <200ms  confidence >= 0.80
    4. Historical baseline <300ms  confidence >= 0.75
    5. LLM Judge           <5s     no floor — the last tier; see below

All five Blueprint Part 7.2 tiers are attached — `_TIERS` at the bottom of this
file is where each one is wired in, and where a sixth would go were the
Blueprint ever extended.

Tier 3 covers both ways the graph can explain a failure: an exact hash match
via `find_root_causes`, and, when that finds nothing, a semantic-similarity
fallback via `find_similar_signature` (embeddings from
`app.services.diagnostic.embeddings`, DECISIONS.md 2026-08-21) — two failures
that describe the same fault in different words normalize to different hashes
but land near each other in embedding space. The fallback's confidence is
discounted by how similar the match actually was; see `_tier_fkg_similarity`.

Tier 5 (`app.services.diagnostic.llm_judge`, routed through OpenRouter rather
than Claude — DECISIONS.md, 2026-08-21) carries no real confidence floor,
unlike tiers 1-4: it is the last tier, so there is nothing left to defer a weak
answer to. Whether it runs at all is gated well before any threshold question —
`llm_judge._rate_gate` checks the feature flag, the API key, and a Redis-backed
daily request cap (the free OpenRouter tier is ~50 req/day/key, so "cost-gated"
in the Blueprint's scope line is a *rate* gate on this stack, not a $ one) —
and the gate fails **closed** on a Redis outage, deliberately the opposite of
`app.core.redis.rate_limit`'s fail-open default.

`DiagnosticEngine` gives the cascade a home, but nothing in the running system
calls `diagnose()` yet — `worker/tasks/polling.py` still runs the Phase 2
detectors inline. Wiring that in is a separate, deliberately-paused decision;
see the coexistence note below and DECISIONS.md for why it is not a drop-in
replacement.

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
from app.services.diagnostic.baseline import check_baseline_anomalies
from app.services.diagnostic.embeddings import embed_query
from app.services.diagnostic.fkg import ingest_failure, normalize_error_message, signature_hash
from app.services.diagnostic.fkg_traversal import (
    best_remediation,
    best_root_cause,
    find_root_causes,
    find_similar_signature,
)
from app.services.diagnostic.llm_judge import judge as judge_llm
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
#
# LLM_JUDGE's floor is 0.0, not a real threshold: Blueprint's own pseudocode
# calls it unconditionally ("if llm_judge_available: return llm_judge(...)"),
# with no confidence comparison, because it is the last tier — there is
# nothing left to defer to if its answer is weak. The floor entry exists so
# the cascade loop's `TIER_THRESHOLDS[tier]` lookup stays uniform across all
# five tiers rather than special-casing the last one. _parse_verdict in
# llm_judge.py is the real gate: anything reaching this floor already passed
# its own 0.0-1.0 validation.
TIER_THRESHOLDS: dict[DiagnosticTier, float] = {
    DiagnosticTier.RULE_BASED: 0.90,
    DiagnosticTier.SCHEMA_CHECK: 0.85,
    DiagnosticTier.FKG_TRAVERSAL: 0.80,
    DiagnosticTier.HISTORICAL_BASELINE: 0.75,
    DiagnosticTier.LLM_JUDGE: 0.0,
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

# Same reasoning as the drift split above, applied to baseline.severity's own
# two values. A "warning" anomaly (too few records, or a run that took far too
# long) is the shape this detector exists to catch and clears the tier floor.
# An "info" anomaly (more records than usual, or an unusually fast run) is, per
# BaselineAnomaly.suggested_fix's own wording, "usually harmless" — it scores
# below the floor so a stronger tier gets the last word instead of this one
# reporting a likely-benign deviation as an explained failure.
_BASELINE_CONFIDENCE_WARNING = 0.80
_BASELINE_CONFIDENCE_INFO = 0.55

# When both metrics are anomalous on the same execution, items_processed is
# reported: a records anomaly points at what the workflow actually did, a
# duration anomaly at how long doing it took — the former is the more direct
# signal for "is this workflow's output wrong" that a diagnosis exists to say.
_METRIC_PRIORITY = {"items_processed": 0, "duration_ms": 1}


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

        An exact hash miss falls back to semantic similarity: "rate limited,
        retry after 30s" and "throttled — please retry in 30 seconds" describe
        the same fault but normalize to different text, so no hash match finds
        either from the other. See `_similarity_fallback` for how that match is
        scored down from an exact one.

        No error message means no signature to look up, and no query is worth
        running — an execution here is only ever a failure (tiers 1-2 already
        had their turn), so a missing message means the platform gave nothing
        to key on, not that the graph was empty.
        """
        error_message = normalized.error_message
        if not error_message or not error_message.strip():
            return None

        sig_hash = signature_hash(error_message)
        matches = await find_root_causes(
            session, workspace_id=workspace_id, signature_hash=sig_hash
        )

        root = best_root_cause(matches)
        if root is None:
            return await self._tier_fkg_similarity(
                session,
                error_message,
                workspace_id=workspace_id,
                workflow_id=normalized.workflow_id,
            )

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

    async def _tier_fkg_similarity(
        self,
        session: AsyncSession,
        error_message: str,
        *,
        workspace_id: uuid.UUID,
        workflow_id: str,
    ) -> TieredDiagnosis | None:
        """
        Semantic fallback within tier 3, not a tier of its own — the Blueprint's
        five tiers name "FKG traversal" once; similarity search is how it finds
        an anchor when the exact-hash lookup found none.

        Takes the already-validated `error_message` rather than the full
        `NormalizedExecution`: the caller narrows `str | None` to `str` right
        before this is invoked, and re-deriving it here from `normalized.
        error_message` would hand mypy the widened optional type straight back.

        Confidence is the matched root cause's own confidence discounted by how
        similar the anchor signature actually was: an inferred match to a
        merely-similar signature is a weaker claim than an identical one, and
        multiplying the two keeps a borderline-similarity match (close to
        `_MIN_SIMILARITY`) from clearing tier 3's floor on the strength of a
        confident root cause it is not actually confident applies here.
        """
        query_vector = await embed_query(normalize_error_message(error_message))
        similar = await find_similar_signature(
            session, workspace_id=workspace_id, embedding=query_vector
        )
        if similar is None:
            return None

        matches = await find_root_causes(
            session, workspace_id=workspace_id, signature_hash=similar.dedup_key
        )
        root = best_root_cause(matches)
        if root is None:
            return None

        remediation = best_remediation(matches)
        logger.info(
            "diagnostic.fkg_similarity_match",
            similarity=similar.similarity,
            root_confidence=root.confidence,
            workflow_id=workflow_id,
        )
        return TieredDiagnosis(
            root_cause=root.label,
            category=root.properties.get("category", "unknown"),
            confidence=round(root.confidence * similar.similarity, 4),
            suggested_fix=(
                root.properties.get("suggested_fix")
                or "No remediation recorded for this root cause yet."
            ),
            tier_used=DiagnosticTier.FKG_TRAVERSAL,
            remediation_playbook=(
                remediation.properties.get("playbook") if remediation else None
            ),
        )

    async def _tier_baseline(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """
        Tier 4 — does this run's items_processed or duration_ms fall outside
        the workflow's own rolling 7-day percentile band.

        check_baseline_anomalies can return two anomalies at once (both metrics
        out of band on the same run); the cascade wants one answer, so this
        picks by _METRIC_PRIORITY when both fire. Confidence is not the
        statistical strength of either deviation — it is the same
        consequence-over-certainty split used for schema drift, keyed off the
        anomaly's own severity.
        """
        anomalies = await check_baseline_anomalies(
            session,
            integration_id=integration_id,
            workflow_id=normalized.workflow_id,
            platform_run_id=normalized.platform_run_id,
            status=normalized.status,
            items_processed=normalized.items_processed,
            duration_ms=normalized.duration_ms,
        )
        if not anomalies:
            return None

        anomaly = min(anomalies, key=lambda a: _METRIC_PRIORITY[a.metric])
        confidence = (
            _BASELINE_CONFIDENCE_WARNING
            if anomaly.severity == "warning"
            else _BASELINE_CONFIDENCE_INFO
        )

        return TieredDiagnosis(
            root_cause=anomaly.describe(),
            category="silent",
            confidence=confidence,
            suggested_fix=anomaly.suggested_fix(),
            tier_used=DiagnosticTier.HISTORICAL_BASELINE,
        )

    async def _tier_llm_judge(
        self,
        session: AsyncSession,
        normalized: NormalizedExecution,
        *,
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID,
        platform: str,
    ) -> TieredDiagnosis | None:
        """
        Tier 5 — the LLM Judge. Reached only when tiers 1-4 each answered below
        their own floor or not at all; everything about whether this call is
        even allowed to happen (feature flag, API key, daily request cap) lives
        in llm_judge._rate_gate, not here.

        category_hint is always None: no earlier tier's sub-threshold result is
        retained for a later tier to read, so there is no signal to hint with.
        Retaining one would be a real improvement but is a cascade-wide change,
        not a tier-5-only one — out of scope here.
        """
        verdict = await judge_llm(
            error_message=normalized.error_message,
            category_hint=None,
            platform=platform,
            workflow_name=normalized.workflow_name,
            workflow_id=normalized.workflow_id,
            status=normalized.status,
            items_processed=normalized.items_processed,
            raw_payload=normalized.raw_payload,
        )
        if verdict is None:
            return None

        return TieredDiagnosis(
            root_cause=verdict.root_cause,
            category=verdict.category,
            confidence=verdict.confidence,
            suggested_fix=verdict.suggested_fix,
            tier_used=DiagnosticTier.LLM_JUDGE,
            remediation_playbook=verdict.remediation_playbook,
            requires_hitl=verdict.requires_hitl,
        )


# Cascade order — all five Blueprint Part 7.2 tiers are attached.
_TIERS = [
    (DiagnosticTier.RULE_BASED, DiagnosticEngine._tier_rule_based),
    (DiagnosticTier.SCHEMA_CHECK, DiagnosticEngine._tier_schema_check),
    (DiagnosticTier.FKG_TRAVERSAL, DiagnosticEngine._tier_fkg_traversal),
    (DiagnosticTier.HISTORICAL_BASELINE, DiagnosticEngine._tier_baseline),
    (DiagnosticTier.LLM_JUDGE, DiagnosticEngine._tier_llm_judge),
]
