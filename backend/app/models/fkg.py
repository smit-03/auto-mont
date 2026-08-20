"""
Failure Knowledge Graph — nodes and edges.

The FKG is a property graph stored in Postgres rather than Neo4j (Blueprint Part
1, Conflict 1): a nodes table, an edges table, JSONB properties, and recursive
CTEs for traversal. This keeps graph semantics without a second datastore.

What it is for: the rule engine only knows failures someone wrote a rule for.
The FKG accumulates what this platform has actually diagnosed, so a signature
seen once in any workspace can explain the same signature seen later in
another — and, with embeddings, a signature that is merely *similar* rather than
identical.

Node identity is the subtle part. Two kinds of node exist:

- **Recurring knowledge** (``ErrorSignature``, ``RootCause``, ``SystemComponent``,
  ``RemediationWorkflow``, ``PreventionPolicy``) is deduplicated on ``dedup_key``.
  Seeing the same signature a hundred times must not create a hundred nodes, or
  traversal degree explodes and similarity search returns the same answer a
  hundred times.
- **Occurrences** (``FailureInstance``) are *not* deduplicated: ``dedup_key`` is
  NULL and every diagnosed failure is its own row. This is deliberate and it is
  what makes Phase 3's "FKG accumulates knowledge across 50+ diagnosed failures"
  exit criterion countable. Note this differs from ``alerts``, which collapse
  repeat detections into one incident row — an alert answers "is this broken
  right now?", a FailureInstance answers "how often has this happened?"
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Embedding width. The Blueprint specified 1536 (OpenAI text-embedding-3-small);
# this is 1024, the width of the free OpenRouter embedding models and also of
# bge-large, e5-large, gte-large, mistral-embed and Voyage — so it is the more
# portable choice, not merely the available one. Changing it requires a
# migration *and* a re-embed: vectors of different widths are not comparable,
# and `embedding_model` on each node exists so a stale-width row is detectable
# rather than silently returning garbage similarity.
EMBEDDING_DIMENSIONS = 1024

NODE_TYPES = (
    "FailureInstance",
    "ErrorSignature",
    "RootCause",
    "SystemComponent",
    "RemediationWorkflow",
    "PreventionPolicy",
)

EDGE_TYPES = (
    "HAS_SIGNATURE",
    "EXPLAINS",
    "OCCURRED_IN",
    "RESOLVED_BY",
    "PREVENTED_BY",
)


class FKGNode(Base):
    """A node in the Failure Knowledge Graph."""

    __tablename__ = "fkg_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # NULL means global knowledge, shared across every workspace. Traversal
    # matches `workspace_id = :ws OR workspace_id IS NULL`, so a globally
    # curated root cause explains a failure in any tenant without being copied
    # into each one. Tenant-authored nodes always carry a workspace_id; nothing
    # in the ingest path writes NULL.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # Stable identity for recurring knowledge; NULL for FailureInstance nodes,
    # which are occurrences rather than concepts. The partial unique indexes
    # below are what actually enforce this.
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
    )
    # Which model produced `embedding`. Without it, switching embedding
    # providers leaves a table of vectors from two incompatible spaces that
    # still compare without error and return nonsense neighbours.
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    # How many times this node has been observed. Incremented on dedup hit, so
    # a recurring signature is distinguishable from a one-off without counting
    # FailureInstance rows.
    occurrence_count: Mapped[int] = mapped_column(
        default=1,
        server_default="1",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Declared on the model, not only in the migration: create_all() runs
    # outside production, so a migration-only constraint leaves development
    # unprotected against exactly the duplication this prevents (CLAUDE.md).
    #
    # Two partial indexes rather than one: Postgres treats NULLs as distinct in
    # a unique index, so a single index over (workspace_id, node_type,
    # dedup_key) would not deduplicate global nodes at all — every global node
    # would collide with nothing and be inserted again on each ingest.
    __table_args__ = (
        Index(
            "uq_fkg_nodes_workspace_key",
            "workspace_id",
            "node_type",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL AND workspace_id IS NOT NULL"),
        ),
        Index(
            "uq_fkg_nodes_global_key",
            "node_type",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL AND workspace_id IS NULL"),
        ),
        Index("idx_fkg_nodes_type", "node_type"),
        Index("idx_fkg_nodes_props", "properties", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<FKGNode(id={self.id}, type={self.node_type}, label={self.label[:40]!r})>"


class FKGEdge(Base):
    """A directed, typed relationship between two FKG nodes."""

    __tablename__ = "fkg_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fkg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fkg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(40), nullable=False)
    properties: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    weight: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Ingestion re-asserts the same relationships on every occurrence of a
        # known failure. Without this the edge table grows without bound and
        # traversal returns the same target once per duplicate edge.
        UniqueConstraint(
            "source_id",
            "target_id",
            "edge_type",
            name="uq_fkg_edges_triple",
        ),
        Index("idx_fkg_edges_source", "source_id", "edge_type"),
        Index("idx_fkg_edges_target", "target_id", "edge_type"),
    )

    def __repr__(self) -> str:
        return f"<FKGEdge({self.source_id} -{self.edge_type}-> {self.target_id})>"
