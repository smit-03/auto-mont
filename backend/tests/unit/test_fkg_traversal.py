"""
Unit tests for Failure Knowledge Graph traversal.

The database is faked at the session boundary, matching the convention in
tests/unit/test_baseline.py — the recursive CTE is Postgres's job, not this
module's. What belongs here is the Python side: the empty-graph case,
depth-decay arithmetic, and the shallowest-path tie-breaking in
best_root_cause/best_remediation.
"""

import uuid

import pytest

from app.services.diagnostic.fkg_traversal import (
    _DEPTH_DECAY,
    _MIN_SIMILARITY,
    best_remediation,
    best_root_cause,
    find_root_causes,
    find_similar_signature,
)


def row(node_type, confidence, depth, label="x", properties=None, edge_type="EXPLAINS"):
    return {
        "id": uuid.uuid4(),
        "node_type": node_type,
        "label": label,
        "properties": properties or {},
        "confidence": confidence,
        "edge_type": edge_type,
        "depth": depth,
    }


class FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Returns the queued rows for the traversal CTE, recording each call."""

    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0
        self.last_params: dict | None = None

    async def execute(self, _stmt, params):
        self.execute_calls += 1
        self.last_params = params
        return FakeMappingsResult(self._rows)


async def traverse(session, signature_hash="sig-abc"):
    return await find_root_causes(session, workspace_id=uuid.uuid4(), signature_hash=signature_hash)


class TestEmptyGraph:
    @pytest.mark.asyncio
    async def test_no_rows_returns_empty_list(self):
        session = FakeSession([])

        matches = await traverse(session)

        assert matches == []
        assert session.execute_calls == 1

    @pytest.mark.asyncio
    async def test_best_root_cause_of_empty_matches_is_none(self):
        assert best_root_cause([]) is None

    @pytest.mark.asyncio
    async def test_best_remediation_of_empty_matches_is_none(self):
        assert best_remediation([]) is None


class TestDepthDecay:
    """
    Mutation guard on the `_DEPTH_DECAY ** (depth - 1)` exponent. `depth - 1`
    becoming bare `depth` passes nothing here: a directly-connected root cause
    (depth 1) would silently lose (1 - _DEPTH_DECAY) of its confidence for no
    reason.
    """

    @pytest.mark.asyncio
    async def test_depth_1_is_undecayed(self):
        """The anchor's direct target — depth 1 — carries its own confidence."""
        session = FakeSession([row("RootCause", confidence=0.9, depth=1)])

        matches = await traverse(session)

        assert matches[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_depth_2_decays_by_one_factor(self):
        session = FakeSession([row("RootCause", confidence=0.9, depth=2)])

        matches = await traverse(session)

        assert matches[0].confidence == round(0.9 * _DEPTH_DECAY, 4)
        assert matches[0].confidence < 0.9

    @pytest.mark.asyncio
    async def test_depth_3_decays_by_two_factors(self):
        session = FakeSession([row("RootCause", confidence=0.9, depth=3)])

        matches = await traverse(session)

        assert matches[0].confidence == round(0.9 * _DEPTH_DECAY**2, 4)


class TestNodeTypeFilteringAndShape:
    @pytest.mark.asyncio
    async def test_matches_carry_the_row_fields_through(self):
        session = FakeSession(
            [
                row(
                    "RootCause",
                    confidence=0.9,
                    depth=1,
                    label="OAuth token expired",
                    properties={"category": "auth"},
                )
            ]
        )

        matches = await traverse(session)

        assert len(matches) == 1
        assert matches[0].node_type == "RootCause"
        assert matches[0].label == "OAuth token expired"
        assert matches[0].properties == {"category": "auth"}

    @pytest.mark.asyncio
    async def test_null_properties_become_empty_dict(self):
        """JSONB can come back NULL for a node that was never given properties."""
        r = row("RootCause", confidence=0.9, depth=1)
        r["properties"] = None
        session = FakeSession([r])

        matches = await traverse(session)

        assert matches[0].properties == {}

    @pytest.mark.asyncio
    async def test_query_is_scoped_to_the_requested_signature_and_workspace(self):
        session = FakeSession([])
        workspace_id = uuid.uuid4()

        await find_root_causes(session, workspace_id=workspace_id, signature_hash="sig-xyz")

        assert session.last_params["signature_hash"] == "sig-xyz"
        assert session.last_params["workspace_id"] == str(workspace_id)


class TestBestPicking:
    """min(matches, key=lambda m: (m.depth, -m.confidence)) — shallowest first, then strongest."""

    def match(self, node_type, depth, confidence):
        session_row = row(node_type, confidence, depth)
        # Build the FKGMatch the same way find_root_causes does, without a session.
        from app.services.diagnostic.fkg_traversal import FKGMatch

        return FKGMatch(
            node_id=session_row["id"],
            node_type=node_type,
            label=session_row["label"],
            properties={},
            depth=depth,
            confidence=confidence,
            edge_type=session_row["edge_type"],
        )

    def test_shallower_root_cause_wins_over_a_more_confident_deeper_one(self):
        shallow = self.match("RootCause", depth=1, confidence=0.6)
        deep = self.match("RootCause", depth=3, confidence=0.99)

        assert best_root_cause([deep, shallow]) is shallow

    def test_higher_confidence_wins_at_the_same_depth(self):
        weak = self.match("RootCause", depth=2, confidence=0.5)
        strong = self.match("RootCause", depth=2, confidence=0.8)

        assert best_root_cause([weak, strong]) is strong

    def test_best_root_cause_ignores_remediation_nodes(self):
        remediation = self.match("RemediationWorkflow", depth=1, confidence=0.99)
        cause = self.match("RootCause", depth=2, confidence=0.5)

        assert best_root_cause([remediation, cause]) is cause

    def test_best_remediation_ignores_root_cause_nodes(self):
        cause = self.match("RootCause", depth=1, confidence=0.99)
        remediation = self.match("RemediationWorkflow", depth=2, confidence=0.5)

        assert best_remediation([cause, remediation]) is remediation


class TestFindSimilarSignature:
    """
    find_similar_signature is a single-row nearest-neighbour lookup, not the
    recursive CTE — its own FakeSession stands in the same way, returning a
    canned `(dedup_key, similarity)` row instead of a path.
    """

    async def similar(self, session):
        return await find_similar_signature(
            session, workspace_id=uuid.uuid4(), embedding=[0.1, 0.2, 0.3]
        )

    @pytest.mark.asyncio
    async def test_no_rows_returns_none(self):
        session = FakeSession([])

        result = await self.similar(session)

        assert result is None

    @pytest.mark.asyncio
    async def test_strong_match_is_returned(self):
        session = FakeSession([{"dedup_key": "sig-abc", "similarity": 0.95}])

        result = await self.similar(session)

        assert result is not None
        assert result.dedup_key == "sig-abc"
        assert result.similarity == 0.95

    @pytest.mark.asyncio
    async def test_similarity_exactly_at_the_floor_is_accepted(self):
        session = FakeSession([{"dedup_key": "sig-abc", "similarity": _MIN_SIMILARITY}])

        result = await self.similar(session)

        assert result is not None

    @pytest.mark.asyncio
    async def test_similarity_a_hair_below_the_floor_is_rejected(self):
        """
        Mutation guard on `similarity < _MIN_SIMILARITY`: the nearest neighbour
        the query found is not close enough to trust as the same failure.
        """
        session = FakeSession([{"dedup_key": "sig-abc", "similarity": _MIN_SIMILARITY - 0.01}])

        result = await self.similar(session)

        assert result is None

    @pytest.mark.asyncio
    async def test_embedding_is_passed_as_a_pgvector_literal(self):
        """
        Guards the `_vector_literal` formatting: a raw Python list handed to
        asyncpg for a `::vector`-cast text parameter would fail to bind, not
        silently misbehave — but a malformed literal (wrong brackets, stray
        Python repr artifacts) would only be caught by a live database.
        """
        session = FakeSession([])

        await find_similar_signature(session, workspace_id=uuid.uuid4(), embedding=[1.0, -0.5, 0.0])

        literal = session.last_params["query_embedding"]
        assert literal == "[1.0,-0.5,0.0]"

    @pytest.mark.asyncio
    async def test_workspace_id_is_scoped_in_the_query_params(self):
        session = FakeSession([])
        workspace_id = uuid.uuid4()

        await find_similar_signature(session, workspace_id=workspace_id, embedding=[0.1])

        assert session.last_params["workspace_id"] == str(workspace_id)
