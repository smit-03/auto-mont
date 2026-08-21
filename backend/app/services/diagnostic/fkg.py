"""
Failure Knowledge Graph ingestion.

Every diagnosed failure writes a small subgraph:

    FailureInstance --HAS_SIGNATURE--> ErrorSignature --EXPLAINS--> RootCause
             |                                                          |
             +--------OCCURRED_IN--> SystemComponent                    |
                                                          RESOLVED_BY   v
                                                     RemediationWorkflow

The edge directions are not arbitrary. Traversal enters at an ErrorSignature and
walks *forward*, so a root cause must be reachable from its signature in one hop
and a remediation in two. Reversing any of these makes the Blueprint's recursive
CTE return nothing while still being a perfectly valid graph.

Why signatures are normalized before hashing: raw platform error messages embed
run ids, timestamps, row counts and quoted values, so two occurrences of the
same fault almost never share a byte-identical message. Hashing the raw text
would produce a graph with one signature node per execution — technically
correct and completely useless, since nothing would ever match anything.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.fkg import FKGEdge, FKGNode
from app.services.diagnostic.embeddings import EMBEDDING_MODEL_NAME, embed_passage

logger = get_logger(__name__)

# Volatile fragments, replaced before hashing. Order matters: UUIDs and ISO
# timestamps are matched before the bare-number rule, which would otherwise chew
# them into pieces and leave a signature that still varies per run.
_VOLATILE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        "<uuid>",
    ),
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"),
        "<timestamp>",
    ),
    (re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE), "<hex>"),
    # Long hex runs: request ids, content hashes, trace ids.
    (re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE), "<hash>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
    (re.compile(r"'[^']*'"), "'<value>'"),
    (re.compile(r'"[^"]*"', re.DOTALL), '"<value>"'),
    # Numbers are only *partly* volatile, and getting this wrong is costly in
    # both directions. Collapsing every number merges "status 401" and
    # "status 429" into one signature, fusing an auth failure with a rate-limit
    # failure. Collapsing none leaves "timed out after 30012ms" unique per run.
    #
    # So: strip numbers that carry a unit suffix (durations, sizes) and numbers
    # of four or more digits (epochs, ids, byte counts, row counts), and keep
    # short bare numbers, which is where HTTP status codes and retry counts
    # live.
    (re.compile(r"\d+(?=\s?(?:ms|s|m|h|ns|us|kb|mb|gb|tb|b)\b)", re.IGNORECASE), "<n>"),
    (re.compile(r"\b\d{4,}\b"), "<n>"),
)

_MAX_SIGNATURE_LENGTH = 500


def normalize_error_message(error_message: str) -> str:
    """
    Reduce an error message to its run-invariant shape.

    "Request 4f2c-... timed out after 30012ms" and "Request 9a1b-... timed out
    after 29998ms" both become "Request <uuid> timed out after <n>ms", so the
    two occurrences share one signature node.
    """
    text = error_message.strip()
    for pattern, replacement in _VOLATILE_PATTERNS:
        text = pattern.sub(replacement, text)
    # Collapse whitespace last: the substitutions above can leave runs of it.
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_SIGNATURE_LENGTH]


def signature_hash(error_message: str) -> str:
    """Stable hash of the normalized signature. Used as the node's dedup_key."""
    return hashlib.sha256(normalize_error_message(error_message).encode("utf-8")).hexdigest()[:48]


def _content_key(prefix: str, value: str) -> str:
    """Dedup key for a node identified by its text rather than by an id."""
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:40]
    return f"{prefix}:{digest}"


@dataclass
class IngestedFailure:
    """What one ingestion wrote, for logging and for the caller's assertions."""

    failure_instance_id: uuid.UUID
    signature_node_id: uuid.UUID
    signature_hash: str
    root_cause_node_id: uuid.UUID | None
    is_new_signature: bool


async def _upsert_node(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    node_type: str,
    label: str,
    dedup_key: str,
    properties: dict | None = None,
    confidence: float = 1.0,
) -> tuple[uuid.UUID, bool]:
    """
    Insert a recurring-knowledge node, or bump the one already there.

    Returns (node_id, was_created). A single INSERT ... ON CONFLICT rather than
    a select-then-insert: the polling path runs concurrently across
    integrations, and two workers diagnosing the same signature at once would
    otherwise race to create duplicate nodes that the unique index then rejects,
    failing the whole ingest transaction.
    """
    stmt = (
        pg_insert(FKGNode)
        .values(
            workspace_id=workspace_id,
            node_type=node_type,
            label=label,
            dedup_key=dedup_key,
            properties=properties or {},
            confidence=confidence,
            occurrence_count=1,
        )
        .on_conflict_do_update(
            # Names the partial index matching workspace-scoped nodes. Ingestion
            # never writes global (workspace_id IS NULL) nodes, so the global
            # index is not a candidate arbiter here.
            index_where=FKGNode.dedup_key.isnot(None) & FKGNode.workspace_id.isnot(None),
            index_elements=[FKGNode.workspace_id, FKGNode.node_type, FKGNode.dedup_key],
            set_={
                "occurrence_count": FKGNode.occurrence_count + 1,
                "updated_at": func.now(),
            },
        )
        .returning(FKGNode.id, FKGNode.occurrence_count)
    )
    row = (await session.execute(stmt)).one()
    return row[0], row[1] == 1


async def _insert_edge(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    edge_type: str,
    weight: float = 1.0,
) -> None:
    """
    Assert a relationship, ignoring it if already present.

    Ingestion re-states the same edges on every recurrence of a known failure,
    so conflict-do-nothing is the normal path, not the exceptional one.
    """
    if source_id == target_id:
        # A self-edge would make the traversal CTE cycle until its depth guard
        # fires, returning the same node five times.
        return

    await session.execute(
        pg_insert(FKGEdge)
        .values(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            weight=weight,
        )
        .on_conflict_do_nothing(constraint="uq_fkg_edges_triple")
    )


async def ingest_failure(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    error_message: str | None,
    category: str,
    root_cause: str | None,
    confidence: float = 1.0,
    tier: str = "rule_based",
    suggested_fix: str | None = None,
    remediation_playbook: str | None = None,
    platform: str | None = None,
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    error_node: str | None = None,
    execution_id: uuid.UUID | None = None,
    platform_run_id: str | None = None,
) -> IngestedFailure | None:
    """
    Write one diagnosed failure into the graph.

    Returns None when there is nothing to key a signature on. A failure with no
    error message cannot be matched against anything later, and inserting a
    signature node for the empty string would collapse every such failure onto
    one meaningless hub node that then "explains" all of them.

    Caller owns the transaction. This is intentional: the graph write must land
    with the alert that produced it, in the same commit as the schema-drift
    baseline advance, so a crash cannot leave a failure recorded as diagnosed
    with no corresponding knowledge.
    """
    if not error_message or not error_message.strip():
        return None

    sig_hash = signature_hash(error_message)
    normalized = normalize_error_message(error_message)

    signature_node_id, is_new = await _upsert_node(
        session,
        workspace_id=workspace_id,
        node_type="ErrorSignature",
        label=normalized,
        dedup_key=sig_hash,
        properties={
            "signature_hash": sig_hash,
            "category": category,
            # The raw text of one occurrence, kept for a human reading the node.
            # Never used for matching — that is what `label` is for.
            "sample_error_message": error_message[:2000],
        },
        confidence=confidence,
    )

    if is_new:
        # Only new signatures are embedded. `label` is the normalized text and
        # is identical for every recurrence of the same signature, so
        # re-embedding a recurrence would spend a CPU encode to produce the
        # vector already stored under this node. Not "only sometimes embed" —
        # every signature gets one, exactly once, on the ingestion that first
        # creates it.
        embedding = await embed_passage(normalized)
        await set_node_embedding(
            session,
            node_id=signature_node_id,
            embedding=embedding,
            model=EMBEDDING_MODEL_NAME,
        )

    # FailureInstance is deliberately not deduplicated: one row per occurrence.
    # This is the table Phase 3's "50+ diagnosed failures" criterion counts.
    failure_instance = FKGNode(
        workspace_id=workspace_id,
        node_type="FailureInstance",
        label=f"{category} failure in {workflow_name or workflow_id or 'unknown workflow'}",
        dedup_key=None,
        properties={
            "category": category,
            "tier": tier,
            "confidence": confidence,
            "platform": platform,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "error_node": error_node,
            "execution_id": str(execution_id) if execution_id else None,
            "platform_run_id": platform_run_id,
        },
        confidence=confidence,
    )
    session.add(failure_instance)
    # Needed now: the edges below reference this id, and without a flush it is
    # still None at insert time.
    await session.flush()

    await _insert_edge(
        session,
        source_id=failure_instance.id,
        target_id=signature_node_id,
        edge_type="HAS_SIGNATURE",
    )

    root_cause_node_id: uuid.UUID | None = None
    if root_cause and root_cause.strip():
        root_cause_node_id, _ = await _upsert_node(
            session,
            workspace_id=workspace_id,
            node_type="RootCause",
            label=root_cause.strip()[:_MAX_SIGNATURE_LENGTH],
            dedup_key=_content_key("rc", root_cause),
            properties={
                "category": category,
                "suggested_fix": suggested_fix,
            },
            confidence=confidence,
        )
        await _insert_edge(
            session,
            source_id=signature_node_id,
            target_id=root_cause_node_id,
            edge_type="EXPLAINS",
            weight=confidence,
        )

    if remediation_playbook and root_cause_node_id:
        playbook_node_id, _ = await _upsert_node(
            session,
            workspace_id=workspace_id,
            node_type="RemediationWorkflow",
            label=remediation_playbook,
            dedup_key=f"playbook:{remediation_playbook}",
            properties={"playbook": remediation_playbook},
        )
        await _insert_edge(
            session,
            source_id=root_cause_node_id,
            target_id=playbook_node_id,
            edge_type="RESOLVED_BY",
            weight=confidence,
        )

    # The component that failed: the specific node when the platform named one,
    # otherwise the workflow. Keyed by both so a workflow's nodes do not collide
    # across workflows with the same node names ("HTTP Request" is everywhere).
    component = error_node or workflow_name or workflow_id
    if component:
        component_node_id, _ = await _upsert_node(
            session,
            workspace_id=workspace_id,
            node_type="SystemComponent",
            label=component,
            dedup_key=_content_key("component", f"{platform}:{workflow_id}:{component}"),
            properties={
                "platform": platform,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "is_node": bool(error_node),
            },
        )
        await _insert_edge(
            session,
            source_id=failure_instance.id,
            target_id=component_node_id,
            edge_type="OCCURRED_IN",
        )

    logger.info(
        "fkg.ingested",
        workspace_id=str(workspace_id),
        signature_hash=sig_hash,
        category=category,
        new_signature=is_new,
        tier=tier,
    )

    return IngestedFailure(
        failure_instance_id=failure_instance.id,
        signature_node_id=signature_node_id,
        signature_hash=sig_hash,
        root_cause_node_id=root_cause_node_id,
        is_new_signature=is_new,
    )


async def count_failure_instances(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """
    How many diagnosed failures the graph holds for a workspace.

    Exists for the Phase 3 exit criterion, which is stated as a count of
    accumulated diagnoses rather than of nodes or alerts.
    """
    return (
        await session.execute(
            select(func.count())
            .select_from(FKGNode)
            .where(
                FKGNode.workspace_id == workspace_id,
                FKGNode.node_type == "FailureInstance",
            )
        )
    ).scalar_one()


async def set_node_embedding(
    session: AsyncSession,
    *,
    node_id: uuid.UUID,
    embedding: list[float],
    model: str,
) -> None:
    """Attach an embedding to a node, recording which model produced it."""
    await session.execute(
        update(FKGNode)
        .where(FKGNode.id == node_id)
        .values(embedding=embedding, embedding_model=model)
    )
