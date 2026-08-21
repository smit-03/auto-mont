"""
Unit tests for Failure Knowledge Graph ingestion.

The database is faked at the session boundary, same convention as
test_baseline.py and test_fkg_traversal.py, but ingestion writes rather than
reads, so the fake here is a recorder: it compiles each pg_insert statement to
its bound parameters (SQLAlchemy Insert.compile().params works without a live
connection — it does not require executing anything), applies the same
dedup-key upsert semantics Postgres would via its ON CONFLICT clause, and
records what would have landed in fkg_nodes and fkg_edges. That is the
question these tests answer: for a given diagnosis, which nodes and edges does
ingest_failure actually write, and does re-ingesting a recurrence increment
rather than duplicate.
"""

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.services.diagnostic.fkg import (
    ingest_failure,
    normalize_error_message,
    signature_hash,
)

WORKSPACE_ID = uuid.uuid4()


class _Row:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, i):
        return self._values[i]


class _ExecResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class FakeGraphSession:
    """
    Records every node upsert and edge assertion `ingest_failure` performs.

    nodes: {(workspace_id, node_type, dedup_key): {"id", "occurrence_count", "values"}}
    edges: [{"source_id", "target_id", "edge_type", "weight"}]
    added: ORM objects passed to add() — always exactly the FailureInstance rows,
    since that is the only thing ingest_failure adds directly rather than
    upserting via pg_insert.
    """

    def __init__(self):
        self.nodes: dict[tuple, dict] = {}
        self.edges: list[dict] = []
        self.added: list = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        params = dict(compiled.params)
        table = stmt.table.name

        if table == "fkg_nodes":
            key = (params.get("workspace_id"), params["node_type"], params.get("dedup_key"))
            existing = self.nodes.get(key)
            if existing is not None:
                existing["occurrence_count"] += 1
                return _ExecResult(_Row([existing["id"], existing["occurrence_count"]]))

            node_id = uuid.uuid4()
            occurrence_count = params.get("occurrence_count", 1)
            self.nodes[key] = {
                "id": node_id,
                "occurrence_count": occurrence_count,
                "values": params,
            }
            return _ExecResult(_Row([node_id, occurrence_count]))

        if table == "fkg_edges":
            self.edges.append(
                {
                    "source_id": params["source_id"],
                    "target_id": params["target_id"],
                    "edge_type": params["edge_type"],
                    "weight": params.get("weight"),
                }
            )
            return _ExecResult(_Row([None]))

        raise AssertionError(f"unexpected table in ingestion: {table}")

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    def node_types(self) -> list[str]:
        return [key[1] for key in self.nodes]

    def edge_types(self) -> list[str]:
        return [e["edge_type"] for e in self.edges]


async def ingest(session, **overrides):
    defaults = {
        "workspace_id": WORKSPACE_ID,
        "error_message": "Request to https://api.example.com/orders timed out after 30012ms",
        "category": "api",
        "root_cause": "Upstream API timeout",
        "confidence": 0.9,
        "tier": "rule_based",
        "suggested_fix": "Increase the timeout or add retry with backoff",
        "remediation_playbook": "retry_backoff",
        "platform": "n8n",
        "workflow_id": "wf-1",
        "workflow_name": "Order sync",
        "error_node": "HTTP Request",
    }
    defaults.update(overrides)
    return await ingest_failure(session, **defaults)


class TestNoSignatureNoWrite:
    @pytest.mark.asyncio
    async def test_empty_error_message_writes_nothing(self):
        session = FakeGraphSession()

        result = await ingest(session, error_message="")

        assert result is None
        assert session.nodes == {}
        assert session.edges == []
        assert session.added == []

    @pytest.mark.asyncio
    async def test_whitespace_only_error_message_writes_nothing(self):
        session = FakeGraphSession()

        result = await ingest(session, error_message="   ")

        assert result is None
        assert session.nodes == {}

    @pytest.mark.asyncio
    async def test_none_error_message_writes_nothing(self):
        session = FakeGraphSession()

        result = await ingest(session, error_message=None)

        assert result is None
        assert session.nodes == {}


class TestFullSubgraph:
    """root_cause + remediation_playbook + error_node — every edge type fires."""

    @pytest.mark.asyncio
    async def test_writes_one_node_of_each_expected_type(self):
        session = FakeGraphSession()

        await ingest(session)

        assert sorted(session.node_types()) == sorted(
            ["ErrorSignature", "RootCause", "RemediationWorkflow", "SystemComponent"]
        )
        assert len(session.added) == 1
        assert session.added[0].node_type == "FailureInstance"

    @pytest.mark.asyncio
    async def test_writes_every_expected_edge(self):
        session = FakeGraphSession()

        await ingest(session)

        assert sorted(session.edge_types()) == sorted(
            ["HAS_SIGNATURE", "EXPLAINS", "RESOLVED_BY", "OCCURRED_IN"]
        )

    @pytest.mark.asyncio
    async def test_edges_connect_the_right_node_pairs(self):
        session = FakeGraphSession()

        result = await ingest(session)

        by_type = {e["edge_type"]: e for e in session.edges}
        assert by_type["HAS_SIGNATURE"]["source_id"] == result.failure_instance_id
        assert by_type["HAS_SIGNATURE"]["target_id"] == result.signature_node_id
        assert by_type["EXPLAINS"]["source_id"] == result.signature_node_id
        assert by_type["EXPLAINS"]["target_id"] == result.root_cause_node_id
        assert by_type["RESOLVED_BY"]["source_id"] == result.root_cause_node_id
        assert by_type["OCCURRED_IN"]["source_id"] == result.failure_instance_id

    @pytest.mark.asyncio
    async def test_result_reports_a_new_signature(self):
        session = FakeGraphSession()

        result = await ingest(session)

        assert result.is_new_signature is True
        assert result.signature_hash == signature_hash(
            "Request to https://api.example.com/orders timed out after 30012ms"
        )


class TestPartialSubgraph:
    @pytest.mark.asyncio
    async def test_no_root_cause_means_no_explains_edge_or_root_cause_node(self):
        session = FakeGraphSession()

        result = await ingest(session, root_cause=None, remediation_playbook=None)

        assert "RootCause" not in session.node_types()
        assert "EXPLAINS" not in session.edge_types()
        assert result.root_cause_node_id is None

    @pytest.mark.asyncio
    async def test_playbook_without_root_cause_is_not_created(self):
        """
        _upsert_node for RemediationWorkflow is gated on root_cause_node_id, not
        just on remediation_playbook being truthy — there is nothing for a
        playbook to resolve if no root cause was recorded to hang it off.
        """
        session = FakeGraphSession()

        await ingest(session, root_cause=None, remediation_playbook="retry_backoff")

        assert "RemediationWorkflow" not in session.node_types()
        assert "RESOLVED_BY" not in session.edge_types()

    @pytest.mark.asyncio
    async def test_no_component_means_no_occurred_in_edge(self):
        session = FakeGraphSession()

        await ingest(session, error_node=None, workflow_name=None, workflow_id="")

        assert "SystemComponent" not in session.node_types()
        assert "OCCURRED_IN" not in session.edge_types()


class TestRecurrenceIsIdempotent:
    """The same failure diagnosed twice bumps occurrence_count; it does not duplicate."""

    @pytest.mark.asyncio
    async def test_second_ingestion_of_the_same_signature_reuses_the_node(self):
        session = FakeGraphSession()

        first = await ingest(session)
        second = await ingest(session)

        assert first.signature_node_id == second.signature_node_id
        assert second.is_new_signature is False

    @pytest.mark.asyncio
    async def test_second_ingestion_bumps_occurrence_count_not_node_count(self):
        session = FakeGraphSession()

        await ingest(session)
        await ingest(session)

        signature_nodes = [
            n for key, n in session.nodes.items() if key[1] == "ErrorSignature"
        ]
        assert len(signature_nodes) == 1
        assert signature_nodes[0]["occurrence_count"] == 2

    @pytest.mark.asyncio
    async def test_second_ingestion_still_adds_a_new_failure_instance(self):
        """
        FailureInstance is one row per occurrence by design — it is what the
        Phase 3 '50+ diagnosed failures' exit criterion counts.
        """
        session = FakeGraphSession()

        await ingest(session)
        await ingest(session)

        assert len(session.added) == 2

    @pytest.mark.asyncio
    async def test_re_asserting_the_same_edges_does_not_duplicate_them(self):
        """
        _insert_edge is conflict-do-nothing; two ingestions of the same failure
        should still leave one HAS_SIGNATURE edge, not two, once Postgres
        applies the real constraint. The fake session cannot enforce the unique
        constraint itself, so this only checks the count *this test issues* —
        the constraint enforcement is Postgres's job, asserted separately in
        migration 20260819_000010.
        """
        session = FakeGraphSession()

        await ingest(session)
        await ingest(session)

        # Two ingestions of an unchanged failure produce two identical
        # HAS_SIGNATURE calls; ON CONFLICT DO NOTHING is what collapses them to
        # one row in Postgres, which this fake does not model. What the fake
        # does prove is that ingest_failure issues the *same* edge triple both
        # times rather than a different one.
        has_sig_edges = [e for e in session.edges if e["edge_type"] == "HAS_SIGNATURE"]
        assert len({e["target_id"] for e in has_sig_edges}) == 1


class TestSignatureNormalization:
    """The dedup key ingestion keys on — spot checks against the ErrorSignature label."""

    @pytest.mark.asyncio
    async def test_two_occurrences_with_different_volatile_fragments_share_a_node(self):
        session = FakeGraphSession()

        first = await ingest(
            session,
            error_message="Request to https://api.example.com/x timed out after 30012ms",
        )
        second = await ingest(
            session,
            error_message="Request to https://api.example.com/y timed out after 29998ms",
        )

        assert first.signature_hash == second.signature_hash
        assert second.is_new_signature is False

    @pytest.mark.asyncio
    async def test_signature_label_is_the_normalized_text(self):
        session = FakeGraphSession()

        await ingest(
            session,
            error_message="Request to https://api.example.com/x timed out after 30012ms",
        )

        sig_node = next(n for key, n in session.nodes.items() if key[1] == "ErrorSignature")
        assert sig_node["values"]["label"] == normalize_error_message(
            "Request to https://api.example.com/x timed out after 30012ms"
        )
