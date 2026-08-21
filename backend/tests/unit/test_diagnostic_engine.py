"""
Cascade behaviour of the multi-tier DiagnosticEngine.

These tests are about the *cascade*, not the detectors or the graph. Tiers 1-3
wrap logic that already has its own suites (test_rule_engine,
test_schema_checker, test_fkg_traversal, test_fkg); what is untested until here
is the part the wrappers and the cascade itself added — which tier gets a
turn, when the walk stops, what happens when nothing answers, and whether a
concluded diagnosis actually reaches the graph.

The threshold comparisons are pinned at their exact boundaries. `>=` silently
becoming `>` is the classic cascade bug: everything still works except an answer
landing exactly on its floor, which then falls through to a more expensive tier
and, once tier 5 exists, spends money to re-derive what tier 1 already knew.

`find_root_causes` and `ingest_failure` are monkeypatched for every test via the
autouse `fkg` fixture below, standing in for an empty graph and a no-op write by
default — real implementations would touch `session`, which these tests pass as
None. Tests that care about tier 3 or ingestion set `fkg.matches` or read
`fkg.ingest_calls`.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services.diagnostic import engine as engine_module
from app.services.diagnostic.engine import (
    TIER_THRESHOLDS,
    DiagnosticEngine,
    DiagnosticTier,
)
from app.services.diagnostic.fkg_traversal import FKGMatch
from app.services.diagnostic.rule_engine import RuleMatch
from app.services.diagnostic.schema_checker import SchemaDrift
from app.services.sentinel.base_adapter import NormalizedExecution

WORKSPACE_ID = uuid.uuid4()
INTEGRATION_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def fkg(monkeypatch):
    """Stand in for the graph: an empty read and a recorded, no-op write."""
    state = SimpleNamespace(matches=[], find_calls=0, ingest_calls=[])

    async def fake_find_root_causes(session, **kwargs):
        state.find_calls += 1
        return state.matches

    async def fake_ingest_failure(session, **kwargs):
        state.ingest_calls.append(kwargs)
        return None

    monkeypatch.setattr(engine_module, "find_root_causes", fake_find_root_causes)
    monkeypatch.setattr(engine_module, "ingest_failure", fake_ingest_failure)
    return state


def fkg_match(
    node_type: str = "RootCause",
    confidence: float = 0.9,
    label: str = "Credential expired",
    properties: dict | None = None,
    depth: int = 1,
) -> FKGMatch:
    return FKGMatch(
        node_id=uuid.uuid4(),
        node_type=node_type,
        label=label,
        properties=properties or {},
        depth=depth,
        confidence=confidence,
        edge_type="EXPLAINS" if node_type == "RootCause" else "RESOLVED_BY",
    )


def execution(**overrides) -> NormalizedExecution:
    defaults = {
        "platform_run_id": "run-1",
        "workflow_id": "wf-1",
        "workflow_name": "Nightly sync",
        "status": "error",
        "started_at": None,
        "finished_at": None,
        "duration_ms": 1200,
        "node_count": 4,
        "items_processed": 0,
        "error_message": "401 Unauthorized",
        "error_node": "HTTP Request",
        "raw_payload": {},
    }
    defaults.update(overrides)
    return NormalizedExecution(**defaults)


class FakeRuleEngine:
    """Stands in for DiagnosticRuleEngine with a controllable verdict."""

    def __init__(self, match: RuleMatch | None):
        self._match = match
        self.calls = 0

    def diagnose(self, execution_dict: dict) -> RuleMatch | None:
        self.calls += 1
        return self._match


def rule_match(confidence: float, category: str = "auth") -> RuleMatch:
    return RuleMatch(
        rule_id="test_rule",
        category=category,
        root_cause="Credential expired",
        confidence=confidence,
        suggested_fix="Reconnect the account",
        remediation_playbook="oauth_refresh",
        requires_hitl=False,
    )


def breaking_drift() -> SchemaDrift:
    """Removals break downstream consumers — scores above the tier 2 floor."""
    return SchemaDrift(
        workflow_id="wf-1",
        removed={"customer.email": "string"},
        previous_fingerprint="aaa",
        current_fingerprint="bbb",
    )


def additive_drift() -> SchemaDrift:
    """A new field is harmless — scores below the floor so deeper tiers run."""
    return SchemaDrift(
        workflow_id="wf-1",
        added={"customer.nickname": "string"},
        previous_fingerprint="aaa",
        current_fingerprint="bbb",
    )


def patch_drift(monkeypatch, drift: SchemaDrift | None) -> dict:
    """Replace tier 2's checker and record whether it was reached."""
    state = {"calls": 0}

    async def fake_check_schema_drift(session, **kwargs):
        state["calls"] += 1
        return drift

    monkeypatch.setattr(engine_module, "check_schema_drift", fake_check_schema_drift)
    return state


async def run(engine: DiagnosticEngine):
    return await engine.diagnose(
        session=None,
        normalized=execution(),
        workspace_id=WORKSPACE_ID,
        integration_id=INTEGRATION_ID,
        platform="n8n",
    )


class TestShortCircuit:
    """A confident tier ends the walk; later tiers must not run."""

    @pytest.mark.asyncio
    async def test_confident_tier_1_returns_without_reaching_tier_2(self, monkeypatch, fkg):
        drift_state = patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(0.95)))

        result = await run(engine)

        assert result is not None
        assert result.tier_used is DiagnosticTier.RULE_BASED
        assert result.root_cause == "Credential expired"
        assert drift_state["calls"] == 0, "tier 2 ran despite tier 1 answering"
        assert fkg.find_calls == 0, "tier 3 ran despite tier 1 answering"

    @pytest.mark.asyncio
    async def test_tier_1_result_carries_playbook_and_hitl_through(self, monkeypatch):
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(0.95)))

        result = await run(engine)

        assert result.remediation_playbook == "oauth_refresh"
        assert result.requires_hitl is False


class TestFallThrough:
    """A tier that answers below its floor does not stop the cascade."""

    @pytest.mark.asyncio
    async def test_below_floor_tier_1_lets_tier_2_answer(self, monkeypatch):
        drift_state = patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(0.5)))

        result = await run(engine)

        assert drift_state["calls"] == 1, "tier 2 never got a turn"
        assert result is not None
        assert result.tier_used is DiagnosticTier.SCHEMA_CHECK

    @pytest.mark.asyncio
    async def test_silent_tier_1_lets_tier_2_answer(self, monkeypatch):
        """No match at all is different from a weak match, and behaves the same."""
        patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result.tier_used is DiagnosticTier.SCHEMA_CHECK


class TestThresholdBoundaries:
    """
    Mutation guards on `confidence >= threshold`.

    `>=` → `>` passes every other test in this file and fails these two.
    """

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_floor_short_circuits(self, monkeypatch):
        floor = TIER_THRESHOLDS[DiagnosticTier.RULE_BASED]
        drift_state = patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(floor)))

        result = await run(engine)

        assert result.tier_used is DiagnosticTier.RULE_BASED
        assert drift_state["calls"] == 0

    @pytest.mark.asyncio
    async def test_confidence_a_hair_below_floor_falls_through(self, monkeypatch):
        floor = TIER_THRESHOLDS[DiagnosticTier.RULE_BASED]
        drift_state = patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(floor - 0.01)))

        result = await run(engine)

        assert drift_state["calls"] == 1
        assert result.tier_used is DiagnosticTier.SCHEMA_CHECK

    @pytest.mark.asyncio
    async def test_tier_2_floor_is_its_own(self, monkeypatch):
        """
        Tier 2's floor is 0.85, not tier 1's 0.90. A single shared constant would
        pass tier 1's tests and quietly change tier 2's behaviour.
        """
        assert TIER_THRESHOLDS[DiagnosticTier.SCHEMA_CHECK] == 0.85
        assert TIER_THRESHOLDS[DiagnosticTier.RULE_BASED] == 0.90


class TestDriftConfidenceSplit:
    """Breaking drift explains a failure; additive drift does not."""

    @pytest.mark.asyncio
    async def test_breaking_drift_clears_the_floor(self, monkeypatch):
        patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is not None
        assert result.tier_used is DiagnosticTier.SCHEMA_CHECK
        assert result.category == "schema"
        assert result.confidence >= TIER_THRESHOLDS[DiagnosticTier.SCHEMA_CHECK]

    @pytest.mark.asyncio
    async def test_additive_drift_falls_below_the_floor(self, monkeypatch):
        """
        Scores 0.50 deliberately. With no tier 3-5 attached the cascade runs out
        and returns None — an additive field is not an explanation for a failure,
        and must not be reported as one.
        """
        patch_drift(monkeypatch, additive_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is None

    @pytest.mark.asyncio
    async def test_type_change_counts_as_breaking(self, monkeypatch):
        drift = SchemaDrift(
            workflow_id="wf-1",
            type_changed={"order.total": ("number", "string")},
            previous_fingerprint="aaa",
            current_fingerprint="bbb",
        )
        patch_drift(monkeypatch, drift)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is not None
        assert result.confidence >= TIER_THRESHOLDS[DiagnosticTier.SCHEMA_CHECK]


class TestExhaustedCascade:
    @pytest.mark.asyncio
    async def test_no_tier_answers_returns_none(self, monkeypatch, fkg):
        """
        None means "nothing attached explains this", not "healthy". Tier 5 is
        what will eventually escalate this case. fkg.matches defaults to [],
        so tier 3 runs and also finds nothing.
        """
        drift_state = patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is None
        assert drift_state["calls"] == 1, "cascade stopped before exhausting its tiers"
        assert fkg.find_calls == 1, "tier 3 never got a turn"
        assert fkg.ingest_calls == [], "nothing was diagnosed; nothing should be written"

    @pytest.mark.asyncio
    async def test_every_attached_tier_is_reached_when_none_answer(self, monkeypatch, fkg):
        """Guards against a tier being dropped from _TIERS by accident."""
        rule = FakeRuleEngine(None)
        drift_state = patch_drift(monkeypatch, None)

        await run(DiagnosticEngine(rule_engine=rule))

        assert rule.calls == 1
        assert drift_state["calls"] == 1
        assert fkg.find_calls == 1
        assert len(engine_module._TIERS) == 3


class TestFkgTraversalTier:
    """Tier 3: does the graph already explain this signature."""

    @pytest.mark.asyncio
    async def test_empty_error_message_skips_the_lookup(self, monkeypatch, fkg):
        """
        Nothing to hash means nothing to look up. Guards the early-return: if
        it were dropped, find_root_causes would run on a signature hashed from
        an empty string.
        """
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await engine.diagnose(
            session=None,
            normalized=execution(error_message=None),
            workspace_id=WORKSPACE_ID,
            integration_id=INTEGRATION_ID,
            platform="n8n",
        )

        assert result is None
        assert fkg.find_calls == 0

    @pytest.mark.asyncio
    async def test_empty_graph_returns_none(self, monkeypatch, fkg):
        """fkg.matches defaults to [] — the literal empty-graph case."""
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert fkg.find_calls == 1
        assert result is None

    @pytest.mark.asyncio
    async def test_root_cause_match_answers_tier_3(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        fkg.matches = [
            fkg_match(
                confidence=0.9,
                label="OAuth token expired",
                properties={"category": "auth", "suggested_fix": "Reconnect the account"},
            )
        ]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is not None
        assert result.tier_used is DiagnosticTier.FKG_TRAVERSAL
        assert result.root_cause == "OAuth token expired"
        assert result.category == "auth"
        assert result.suggested_fix == "Reconnect the account"
        assert result.confidence == 0.9
        assert result.remediation_playbook is None

    @pytest.mark.asyncio
    async def test_remediation_match_is_wired_into_the_playbook(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        fkg.matches = [
            fkg_match(confidence=0.9, properties={"category": "auth"}),
            fkg_match(
                node_type="RemediationWorkflow",
                confidence=0.85,
                label="oauth_refresh",
                properties={"playbook": "oauth_refresh"},
                depth=2,
            ),
        ]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result.remediation_playbook == "oauth_refresh"

    @pytest.mark.asyncio
    async def test_root_cause_with_no_recorded_fix_gets_a_placeholder(self, monkeypatch, fkg):
        """properties.suggested_fix is only ever set when ingestion recorded one."""
        patch_drift(monkeypatch, None)
        fkg.matches = [fkg_match(confidence=0.9, properties={"category": "auth"})]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result.suggested_fix == "No remediation recorded for this root cause yet."

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_floor_answers(self, monkeypatch, fkg):
        floor = TIER_THRESHOLDS[DiagnosticTier.FKG_TRAVERSAL]
        patch_drift(monkeypatch, None)
        fkg.matches = [fkg_match(confidence=floor, properties={"category": "auth"})]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is not None
        assert result.tier_used is DiagnosticTier.FKG_TRAVERSAL

    @pytest.mark.asyncio
    async def test_confidence_a_hair_below_floor_exhausts_the_cascade(self, monkeypatch, fkg):
        floor = TIER_THRESHOLDS[DiagnosticTier.FKG_TRAVERSAL]
        patch_drift(monkeypatch, None)
        fkg.matches = [fkg_match(confidence=floor - 0.01, properties={"category": "auth"})]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is None, "tier 3 answered below its own floor and nothing else is attached"


class TestIngestionWriteSide:
    """
    Every concluded diagnosis is written into the graph, whichever tier
    produced it. Nothing is written when the cascade is exhausted.
    """

    @pytest.mark.asyncio
    async def test_tier_1_conclusion_is_ingested(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(0.95)))

        await run(engine)

        assert len(fkg.ingest_calls) == 1
        call = fkg.ingest_calls[0]
        assert call["workspace_id"] == WORKSPACE_ID
        assert call["error_message"] == "401 Unauthorized"
        assert call["root_cause"] == "Credential expired"
        assert call["category"] == "auth"
        assert call["confidence"] == 0.95
        assert call["tier"] == "rule_based"
        assert call["remediation_playbook"] == "oauth_refresh"
        assert call["platform"] == "n8n"
        assert call["workflow_id"] == "wf-1"
        assert call["execution_id"] is None

    @pytest.mark.asyncio
    async def test_tier_3_conclusion_is_ingested_with_its_own_tier_name(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        fkg.matches = [fkg_match(confidence=0.9, properties={"category": "auth"})]
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        await run(engine)

        assert len(fkg.ingest_calls) == 1
        assert fkg.ingest_calls[0]["tier"] == "fkg_traversal"

    @pytest.mark.asyncio
    async def test_exhausted_cascade_ingests_nothing(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await run(engine)

        assert result is None
        assert fkg.ingest_calls == []

    @pytest.mark.asyncio
    async def test_schema_diagnosis_falls_back_to_root_cause_as_the_signature_text(
        self, monkeypatch, fkg
    ):
        """
        A schema-check diagnosis has no error_message — drift is only detected
        on successful runs. Guards the `normalized.error_message or
        result.root_cause` fallback: dropping it, or swapping `or` for `and`,
        would hand ingest_failure None and it would silently write nothing.
        """
        patch_drift(monkeypatch, breaking_drift())
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(None))

        result = await engine.diagnose(
            session=None,
            normalized=execution(error_message=None, status="success"),
            workspace_id=WORKSPACE_ID,
            integration_id=INTEGRATION_ID,
            platform="n8n",
        )

        assert result is not None
        assert len(fkg.ingest_calls) == 1
        call = fkg.ingest_calls[0]
        assert call["error_message"] == result.root_cause
        assert call["error_message"] is not None
        assert call["tier"] == "schema_check"

    @pytest.mark.asyncio
    async def test_execution_id_reaches_ingestion_when_provided(self, monkeypatch, fkg):
        patch_drift(monkeypatch, None)
        engine = DiagnosticEngine(rule_engine=FakeRuleEngine(rule_match(0.95)))
        exec_id = uuid.uuid4()

        await engine.diagnose(
            session=None,
            normalized=execution(),
            workspace_id=WORKSPACE_ID,
            integration_id=INTEGRATION_ID,
            platform="n8n",
            execution_id=exec_id,
        )

        assert fkg.ingest_calls[0]["execution_id"] == exec_id
