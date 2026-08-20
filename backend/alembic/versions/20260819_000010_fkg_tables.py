"""Create the Failure Knowledge Graph tables.

Phase 3 deliverable 1. The Blueprint's Part 5 DDL declares fkg_nodes and
fkg_edges, but the initial schema migration never created them — it created the
seven tables Phase 1 needed plus hitl_requests, and the `vector` extension, but
not the graph itself. This adds it.

Three deliberate departures from the Blueprint DDL, each recorded in
DECISIONS.md:

1. **embedding is vector(1024), not vector(1536).** The Blueprint sized the
   column for OpenAI text-embedding-3-small. No OpenAI key exists; the available
   free embedding models are 1024-wide, as are bge-large, e5-large, gte-large,
   mistral-embed and Voyage — so 1024 is the more portable width, not merely the
   affordable one. An `embedding_model` column records which model produced each
   vector, because vectors from two different models compare without error and
   return meaningless neighbours.

2. **A `dedup_key` column with two partial unique indexes**, rather than
   deduplicating on `properties->>'signature_hash'`. Recurring knowledge nodes
   (ErrorSignature, RootCause, ...) must be unique; FailureInstance nodes must
   not be, since each is one occurrence and counting them is what the phase exit
   criterion measures. Two indexes are needed because Postgres treats NULLs as
   distinct in a unique index, so global (workspace_id IS NULL) nodes would
   otherwise never collide and would be re-inserted on every ingest.

3. **No ivfflat index yet.** An ivfflat index built on an empty or near-empty
   table produces poor recall — the lists are fitted to the data present at
   build time. With the graph starting empty, a sequential scan over a few
   hundred vectors is both faster and more accurate. The index is added in a
   later migration once there is a corpus to fit it to.
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# Revision identifiers
revision = "20260819_000010"
down_revision = "20260815_000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create fkg_nodes and fkg_edges."""
    # Idempotent: 20240630_000001 already creates it. Repeated here so this
    # migration stands alone if the graph is ever rebuilt into a fresh database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "fkg_nodes",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Nullable: NULL means global knowledge shared by every workspace.
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "properties",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_fkg_nodes_workspace_id", "fkg_nodes", ["workspace_id"])
    op.create_index("idx_fkg_nodes_type", "fkg_nodes", ["node_type"])
    op.create_index(
        "idx_fkg_nodes_props",
        "fkg_nodes",
        ["properties"],
        postgresql_using="gin",
    )
    op.create_index(
        "uq_fkg_nodes_workspace_key",
        "fkg_nodes",
        ["workspace_id", "node_type", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL AND workspace_id IS NOT NULL"),
    )
    op.create_index(
        "uq_fkg_nodes_global_key",
        "fkg_nodes",
        ["node_type", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL AND workspace_id IS NULL"),
    )

    op.create_table(
        "fkg_edges",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fkg_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fkg_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("edge_type", sa.String(length=40), nullable=False),
        sa.Column(
            "properties",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Ingestion re-asserts the same relationships every time a known failure
        # recurs. Without this the edge table grows without bound and traversal
        # returns each target once per duplicate edge.
        sa.UniqueConstraint(
            "source_id", "target_id", "edge_type", name="uq_fkg_edges_triple"
        ),
    )

    op.create_index("idx_fkg_edges_source", "fkg_edges", ["source_id", "edge_type"])
    op.create_index("idx_fkg_edges_target", "fkg_edges", ["target_id", "edge_type"])


def downgrade() -> None:
    """Drop the graph. Edges first — they reference nodes."""
    op.drop_index("idx_fkg_edges_target", table_name="fkg_edges")
    op.drop_index("idx_fkg_edges_source", table_name="fkg_edges")
    op.drop_table("fkg_edges")

    op.drop_index("uq_fkg_nodes_global_key", table_name="fkg_nodes")
    op.drop_index("uq_fkg_nodes_workspace_key", table_name="fkg_nodes")
    op.drop_index("idx_fkg_nodes_props", table_name="fkg_nodes")
    op.drop_index("idx_fkg_nodes_type", table_name="fkg_nodes")
    op.drop_index("ix_fkg_nodes_workspace_id", table_name="fkg_nodes")
    op.drop_table("fkg_nodes")

    # The `vector` extension is left in place: 20240630_000001 created it and
    # other tables may yet use it. Dropping it here would break that migration's
    # ownership of it.
