"""
Failure Knowledge Graph traversal.

Answers one question: given an error signature, what has this platform already
learned explains it, and what fixed it?

The walk starts at an ErrorSignature node and follows outgoing edges forward,
collecting RootCause and RemediationWorkflow nodes by depth. Depth is the
ranking signal — a root cause one hop from the signature is a direct
explanation; one three hops away is an inference through intermediate nodes and
is correspondingly weaker.

Written as raw SQL rather than assembled through the ORM. The recursive CTE is
the Blueprint's, near-verbatim, and it is far more legible as SQL than as a
chain of `union_all` and `alias` calls — which is also why the Blueprint states
it as SQL.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

# Matches the Blueprint's `WHERE fp.depth < 5`. Beyond this the paths stop
# meaning anything: five hops from a signature in a densely linked graph
# reaches most of the workspace's knowledge, which is indistinguishable from a
# random guess.
_MAX_DEPTH = 5

# Confidence decay per hop. A root cause found two hops out is a weaker claim
# than one found adjacent to the signature, and the diagnostic engine gates on
# confidence thresholds, so depth has to translate into confidence or every
# match would present as equally certain.
_DEPTH_DECAY = 0.85


@dataclass
class FKGMatch:
    """A root cause or remediation reachable from a signature."""

    node_id: uuid.UUID
    node_type: str
    label: str
    properties: dict
    depth: int
    # Node confidence decayed by traversal depth.
    confidence: float
    edge_type: str | None


_TRAVERSAL_SQL = text(
    """
    WITH RECURSIVE fkg_path AS (
        SELECT
            n.id,
            n.node_type,
            n.label,
            n.properties,
            n.confidence,
            e.edge_type,
            e.target_id,
            -- The anchor row *is* the signature we started from, so it sits at
            -- depth 0. Its targets are then depth 1, which makes "depth" read
            -- as hops from the signature rather than rows in the CTE.
            0 AS depth
        FROM fkg_nodes n
        JOIN fkg_edges e ON e.source_id = n.id
        WHERE n.node_type = 'ErrorSignature'
          AND n.dedup_key = :signature_hash
          -- Global knowledge (workspace_id IS NULL) is readable by every
          -- workspace; tenant knowledge only by its owner. Dropping the second
          -- arm of this predicate is a cross-tenant data leak, not a widened
          -- search.
          AND (n.workspace_id = :workspace_id OR n.workspace_id IS NULL)

        UNION ALL

        SELECT
            n2.id,
            n2.node_type,
            n2.label,
            n2.properties,
            n2.confidence,
            e2.edge_type,
            e2.target_id,
            fp.depth + 1
        FROM fkg_path fp
        JOIN fkg_nodes n2 ON n2.id = fp.target_id
        LEFT JOIN fkg_edges e2 ON e2.source_id = n2.id
        WHERE fp.depth < :max_depth
          AND (n2.workspace_id = :workspace_id OR n2.workspace_id IS NULL)
    )
    SELECT DISTINCT ON (id)
        id, node_type, label, properties, confidence, edge_type, depth
    FROM fkg_path
    WHERE node_type IN ('RootCause', 'RemediationWorkflow')
    -- DISTINCT ON keeps the shallowest path to each node: the same root cause
    -- is often reachable by several routes, and the shortest is the strongest
    -- claim. Without this a well-connected node appears once per path.
    ORDER BY id, depth
    """
)


async def find_root_causes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signature_hash: str,
    max_depth: int = _MAX_DEPTH,
) -> list[FKGMatch]:
    """
    Walk the graph from one signature to whatever explains it.

    Returns matches ordered by depth, shallowest first, so the caller can take
    the strongest explanation without re-ranking. An empty list means the graph
    has never seen this signature — which is the signal the LLM Judge tier
    exists to handle, not an error.
    """
    rows = (
        await session.execute(
            _TRAVERSAL_SQL,
            {
                "signature_hash": signature_hash,
                "workspace_id": str(workspace_id),
                "max_depth": max_depth,
            },
        )
    ).mappings().all()

    matches = [
        FKGMatch(
            node_id=row["id"],
            node_type=row["node_type"],
            label=row["label"],
            properties=row["properties"] or {},
            depth=row["depth"],
            confidence=round(float(row["confidence"]) * (_DEPTH_DECAY ** (row["depth"] - 1)), 4),
            edge_type=row["edge_type"],
        )
        for row in rows
    ]

    matches.sort(key=lambda m: (m.depth, -m.confidence))

    logger.debug(
        "fkg.traversal",
        workspace_id=str(workspace_id),
        signature_hash=signature_hash,
        matches=len(matches),
    )
    return matches


def best_root_cause(matches: list[FKGMatch]) -> FKGMatch | None:
    """The strongest RootCause among traversal results, if any."""
    causes = [m for m in matches if m.node_type == "RootCause"]
    if not causes:
        return None
    return min(causes, key=lambda m: (m.depth, -m.confidence))


def best_remediation(matches: list[FKGMatch]) -> FKGMatch | None:
    """The strongest RemediationWorkflow among traversal results, if any."""
    playbooks = [m for m in matches if m.node_type == "RemediationWorkflow"]
    if not playbooks:
        return None
    return min(playbooks, key=lambda m: (m.depth, -m.confidence))


# Below this cosine similarity, the nearest neighbour is not the same fault —
# just the least-different thing the graph happens to hold. Returning it would
# be a plausible-sounding wrong answer, which is worse than no answer: nothing
# downstream can tell "this is confidently the same failure" from "this is the
# closest thing we found."
_MIN_SIMILARITY = 0.85

_SIMILARITY_SQL = text(
    """
    SELECT dedup_key, 1 - (embedding <=> :query_embedding::vector) AS similarity
    FROM fkg_nodes
    WHERE node_type = 'ErrorSignature'
      AND embedding IS NOT NULL
      AND (workspace_id = :workspace_id OR workspace_id IS NULL)
    ORDER BY embedding <=> :query_embedding::vector
    LIMIT 1
    """
)


@dataclass
class SimilarSignature:
    """The nearest known ErrorSignature by embedding distance."""

    dedup_key: str
    similarity: float


def _vector_literal(embedding: list[float]) -> str:
    """pgvector's text input format: `[0.1,0.2,...]`."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def find_similar_signature(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    embedding: list[float],
) -> SimilarSignature | None:
    """
    The nearest known ErrorSignature by embedding, if close enough to be worth
    traversing from.

    Exists for failures that do not hash-match anything: "rate limited, retry
    after 30s" and "throttled — please retry in 30 seconds" describe the same
    fault but normalize to different text, so `find_root_causes` finds nothing
    for either from the other. This is the fallback that finds it anyway —
    the caller is expected to traverse from the returned `dedup_key` using
    `find_root_causes`, exactly as it would from an exact hash match; nothing
    about the depth-decay or best-match logic downstream needs to know the
    anchor was found by similarity rather than by hash.

    None means either nothing in the graph has an embedding yet, or the
    nearest neighbour found was not close enough to trust.
    """
    row = (
        await session.execute(
            _SIMILARITY_SQL,
            {
                "query_embedding": _vector_literal(embedding),
                "workspace_id": str(workspace_id),
            },
        )
    ).mappings().first()

    if row is None or row["similarity"] < _MIN_SIMILARITY:
        return None

    return SimilarSignature(dedup_key=row["dedup_key"], similarity=float(row["similarity"]))
