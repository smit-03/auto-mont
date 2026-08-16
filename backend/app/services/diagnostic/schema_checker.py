"""
Schema drift detection — failure taxonomy class 4 (Data Quality & Schema).

Fingerprints the field shape of a workflow's output and compares it against the
previous successful run. This catches the failure mode that produces no error at
all: an upstream provider renames or drops a field, the workflow keeps exiting
successfully, and the damage only becomes visible days later in the destination
system.

Design (Blueprint Part 12, Phase 2 architecture decisions):

    Store schema fingerprint (sorted field hash) per workflow_id. Alert when
    fingerprint changes between consecutive successful runs.

Two judgment calls the Blueprint leaves open:

1. *Which* output is fingerprinted. A workflow's meaningful shape is what its
   final node emitted, not the union of every intermediate node — fingerprinting
   all nodes means any internal change reports as drift, and the signal drowns.

2. Null handling. A field that is null in one run and populated in the next is a
   nullable field, not a type change. Reporting those would generate an alert
   on any workflow with an optional field, which is most of them. Null
   transitions therefore move the stored baseline but are not drift.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.workflow_schema import WorkflowSchema

logger = get_logger(__name__)

# Nested objects are flattened to dotted paths up to this depth. Deeply nested
# API responses would otherwise produce field maps with thousands of entries,
# where the fingerprint is dominated by structure nobody asserts on.
MAX_FLATTEN_DEPTH = 4

# Only this many items per execution are inspected. The shape is homogeneous
# across items in practice, and a 10k-item run should not cost 10k dict walks.
MAX_ITEMS_SAMPLED = 20

# Type name used for a path whose value was null in every sampled item.
NULL_TYPE = "null"


@dataclass
class SchemaDrift:
    """A detected change in a workflow's output shape."""

    workflow_id: str
    added: dict[str, str] = field(default_factory=dict)
    removed: dict[str, str] = field(default_factory=dict)
    type_changed: dict[str, tuple[str, str]] = field(default_factory=dict)
    previous_fingerprint: str = ""
    current_fingerprint: str = ""
    previous_run_id: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.type_changed)

    def describe(self) -> str:
        """Human-readable summary for the alert body."""
        parts: list[str] = []
        if self.added:
            parts.append("Added: " + ", ".join(f"{p} ({t})" for p, t in sorted(self.added.items())))
        if self.removed:
            parts.append(
                "Removed: " + ", ".join(f"{p} ({t})" for p, t in sorted(self.removed.items()))
            )
        if self.type_changed:
            parts.append(
                "Type changed: "
                + ", ".join(
                    f"{p} ({before} → {after})"
                    for p, (before, after) in sorted(self.type_changed.items())
                )
            )
        return "; ".join(parts)

    @property
    def severity(self) -> str:
        """
        Removals and type changes break downstream consumers; additions do not.

        A new field is almost always additive and harmless — alerting on it at
        the same urgency as a dropped field is how an alert feed becomes noise
        that customers mute.
        """
        return "critical" if (self.removed or self.type_changed) else "info"


def _json_type(value: object) -> str:
    """Map a Python value decoded from JSON to a stable type name."""
    if value is None:
        return NULL_TYPE
    if isinstance(value, bool):  # Must precede int — bool is a subclass of int.
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _flatten(obj: dict, prefix: str, depth: int, out: dict[str, str]) -> None:
    """Flatten a dict into {dotted.path: type_name}, bounded by MAX_FLATTEN_DEPTH."""
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and depth < MAX_FLATTEN_DEPTH:
            # Record the container itself so that replacing an object with a
            # scalar is visible as a type change on the parent path.
            out[path] = "object"
            _flatten(value, path, depth + 1, out)
        else:
            out[path] = _json_type(value)


def build_field_map(items: list[dict]) -> dict[str, str]:
    """
    Build {dotted.path: type} across sampled output items.

    A path present in any item is present in the map. Where items disagree, a
    concrete type beats null: one item having ``discount: null`` while another
    has ``discount: 12.5`` describes a nullable number, not two schemas.
    """
    merged: dict[str, str] = {}

    for item in items[:MAX_ITEMS_SAMPLED]:
        if not isinstance(item, dict):
            continue
        flat: dict[str, str] = {}
        _flatten(item, "", 0, flat)
        for path, type_name in flat.items():
            existing = merged.get(path)
            if existing is None or existing == NULL_TYPE:
                merged[path] = type_name

    return merged


def compute_fingerprint(field_map: dict[str, str]) -> str:
    """SHA-256 over the sorted path:type list. Order-independent by construction."""
    canonical = "\n".join(f"{path}:{field_map[path]}" for path in sorted(field_map))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_field_maps(
    previous: dict[str, str], current: dict[str, str], workflow_id: str
) -> SchemaDrift:
    """
    Compare two field maps into added / removed / type-changed sets.

    Type changes involving null on either side are deliberately excluded: those
    describe a nullable field being populated or omitted, which is ordinary.
    """
    drift = SchemaDrift(workflow_id=workflow_id)

    for path, type_name in current.items():
        if path not in previous:
            drift.added[path] = type_name

    for path, type_name in previous.items():
        if path not in current:
            drift.removed[path] = type_name

    for path, before in previous.items():
        after = current.get(path)
        if after is None or after == before:
            continue
        if before == NULL_TYPE or after == NULL_TYPE:
            continue
        drift.type_changed[path] = (before, after)

    return drift


def extract_output_items(platform: str, raw_payload: dict) -> list[dict]:
    """
    Pull the final node's output records out of a platform's raw payload.

    Platform-specific by necessity — the normalized execution record carries
    counts and status, not the payload shape. Platforms without an extractor
    return no items, which disables drift detection for them rather than
    fingerprinting the wrong thing.
    """
    if not isinstance(raw_payload, dict):
        return []
    if platform == "n8n":
        return _extract_n8n_items(raw_payload)
    return []


def _extract_n8n_items(raw_payload: dict) -> list[dict]:
    """
    Extract output items from an n8n execution payload.

    Shape: data.resultData.runData[nodeName][runIndex].data.main[branch][item].json

    The node is chosen by ``resultData.lastNodeExecuted`` — n8n records which
    node terminated the run, which is exactly the workflow's output. Falling
    back to the last key in runData preserves that intent when the field is
    absent, since runData is populated in execution order.
    """
    data = raw_payload.get("data")
    if not isinstance(data, dict):
        return []
    result_data = data.get("resultData")
    if not isinstance(result_data, dict):
        return []
    run_data = result_data.get("runData")
    if not isinstance(run_data, dict) or not run_data:
        return []

    node_name = result_data.get("lastNodeExecuted")
    if node_name not in run_data:
        node_name = list(run_data)[-1]

    runs = run_data.get(node_name)
    if not isinstance(runs, list):
        return []

    items: list[dict] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_output = run.get("data")
        if not isinstance(run_output, dict):
            continue
        branches = run_output.get("main")
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, list):
                continue
            for entry in branch:
                if isinstance(entry, dict) and isinstance(entry.get("json"), dict):
                    items.append(entry["json"])

    return items


async def check_schema_drift(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    platform: str,
    workflow_id: str,
    platform_run_id: str,
    status: str,
    raw_payload: dict,
) -> SchemaDrift | None:
    """
    Fingerprint this execution's output and compare against the stored baseline.

    Returns the drift when the shape changed in a way worth alerting on, else
    None. The baseline is advanced on every successful run whose fingerprint
    differs, including runs whose only change was a null transition — otherwise
    a nullable field would re-diff on every poll forever.
    """
    # Only successful runs define the schema. See the module docstring on the
    # model for why a failed run's truncated output must not become a baseline.
    if status != "success":
        return None

    items = extract_output_items(platform, raw_payload)
    if not items:
        # No inspectable output. Returning early rather than storing an empty
        # field map matters: an empty baseline would make the next populated
        # run look like every field was added at once.
        return None

    current_fields = build_field_map(items)
    if not current_fields:
        return None
    current_fingerprint = compute_fingerprint(current_fields)
    now = datetime.now(UTC)

    existing = (
        await session.execute(
            select(WorkflowSchema).where(
                WorkflowSchema.integration_id == integration_id,
                WorkflowSchema.workflow_id == workflow_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        # First observation establishes the baseline. It is not drift — there is
        # nothing to have drifted from. ON CONFLICT because two concurrent polls
        # of the same integration reach this branch together.
        await session.execute(
            pg_insert(WorkflowSchema)
            .values(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                integration_id=integration_id,
                workflow_id=workflow_id,
                fingerprint=current_fingerprint,
                fields=current_fields,
                sample_run_id=platform_run_id,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["integration_id", "workflow_id"],
            )
        )
        logger.info(
            "schema.baseline_established",
            integration_id=str(integration_id),
            workflow_id=workflow_id,
            field_count=len(current_fields),
        )
        return None

    if existing.fingerprint == current_fingerprint:
        existing.last_seen_at = now
        return None

    drift = diff_field_maps(dict(existing.fields or {}), current_fields, workflow_id)
    drift.previous_fingerprint = existing.fingerprint
    drift.current_fingerprint = current_fingerprint
    drift.previous_run_id = existing.sample_run_id

    # Advance the baseline whether or not the change is reportable, so a
    # nullable field settling into a concrete type does not re-diff every poll.
    existing.fingerprint = current_fingerprint
    existing.fields = current_fields
    existing.sample_run_id = platform_run_id
    existing.last_seen_at = now

    if drift.is_empty:
        logger.debug(
            "schema.fingerprint_changed_without_drift",
            integration_id=str(integration_id),
            workflow_id=workflow_id,
        )
        return None

    logger.info(
        "schema.drift_detected",
        integration_id=str(integration_id),
        workflow_id=workflow_id,
        added=len(drift.added),
        removed=len(drift.removed),
        type_changed=len(drift.type_changed),
    )
    return drift


def build_drift_fix(drift: SchemaDrift) -> str:
    """Remediation text tailored to which kind of drift occurred."""
    lines: list[str] = []

    if drift.removed:
        lines.append(
            "Fields present in the previous successful run are now absent. Any "
            "downstream node or database column reading them is receiving nulls "
            "without raising an error. Check whether the upstream provider renamed "
            "or deprecated these fields, and confirm records written since the last "
            "successful run are complete."
        )
    if drift.type_changed:
        lines.append(
            "A field changed type between successful runs. Type coercion downstream "
            "may be silently truncating or rejecting values — verify the destination "
            "schema still accepts what is now arriving."
        )
    if drift.added and not (drift.removed or drift.type_changed):
        lines.append(
            "New fields appeared in the workflow output. This is usually a harmless "
            "upstream addition and no action is required; it is reported so the "
            "change is on record if related problems surface later."
        )

    return " ".join(lines)
