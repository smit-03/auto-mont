"""
Historical baseline anomaly detection.

Catches the failure that has no error signature and no schema change: a workflow
that has processed ~4,000 records every morning for a month processes 3 today
and reports success. Nothing in the execution is malformed. The only evidence
that something broke is that the number does not look like the numbers before it.

Blueprint Part 12, Phase 2 architecture decision:

    Calculate rolling 7-day percentiles for items_processed and duration_ms per
    workflow. Alert when current value falls below p10 or exceeds p95.

Percentiles rather than the z-score named in deliverable 9. Execution metrics
are strongly right-skewed and routinely bimodal (a nightly full sync alongside
hourly deltas), and a z-score assumes neither. On that shape the standard
deviation is inflated by the very outliers being looked for, so the threshold
widens exactly when it should tighten. Percentiles make no distributional
assumption. This divergence from the deliverable's wording is deliberate and is
flagged for a Blueprint amendment.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.execution import Execution

logger = get_logger(__name__)

# Rolling window the percentiles are computed over.
BASELINE_WINDOW_DAYS = 7

# Below this many prior runs there is no baseline worth comparing against.
# A percentile over 5 samples is noise, and firing on it would train customers
# to ignore the alert feed during exactly the period they are evaluating us.
MIN_SAMPLE_SIZE = 20

LOWER_PERCENTILE = 0.10
UPPER_PERCENTILE = 0.95

# Which metrics are tracked, and which direction of deviation is actionable.
#
# A collapse in items_processed is the silent-failure signal this platform
# exists to catch, and a duration spike usually means an upstream dependency is
# degrading. The opposite directions are real but rarely urgent, so they are
# recorded at info severity — notification channels default to a min_severity of
# "warning", which means they land in the dashboard without paging anyone.
_METRIC_SEVERITY = {
    ("items_processed", "below"): "warning",
    ("items_processed", "above"): "info",
    ("duration_ms", "above"): "warning",
    ("duration_ms", "below"): "info",
}

_METRIC_COLUMNS = {
    "items_processed": Execution.items_processed,
    "duration_ms": Execution.duration_ms,
}


@dataclass
class BaselineAnomaly:
    """A metric outside its rolling percentile band."""

    workflow_id: str
    metric: str  # items_processed | duration_ms
    direction: str  # below | above
    observed: float
    p10: float
    p95: float
    sample_size: int

    @property
    def severity(self) -> str:
        return _METRIC_SEVERITY.get((self.metric, self.direction), "info")

    @property
    def threshold(self) -> float:
        return self.p10 if self.direction == "below" else self.p95

    def describe(self) -> str:
        label = "items processed" if self.metric == "items_processed" else "duration"
        unit = "ms" if self.metric == "duration_ms" else ""
        comparison = "below the p10" if self.direction == "below" else "above the p95"
        return (
            f"This run's {label} ({self.observed:g}{unit}) is {comparison} of the last "
            f"{BASELINE_WINDOW_DAYS} days ({self.threshold:g}{unit}), measured over "
            f"{self.sample_size} previous successful runs "
            f"(p10 {self.p10:g}{unit}, p95 {self.p95:g}{unit})."
        )

    def suggested_fix(self) -> str:
        if self.metric == "items_processed" and self.direction == "below":
            return (
                "The workflow succeeded but handled far fewer records than normal. Treat "
                "this as a probable silent failure: check whether the trigger or upstream "
                "query returned a short result set, whether a filter condition is now "
                "excluding records it previously passed, and whether an upstream "
                "pagination cursor is stuck. Compare this run's raw payload against a "
                "healthy run from the same time of day."
            )
        if self.metric == "items_processed" and self.direction == "above":
            return (
                "The workflow handled substantially more records than normal. Common "
                "causes are a duplicate or replayed trigger, a backfill running "
                "alongside the regular schedule, or a loop that stopped filtering. Verify "
                "the destination did not receive duplicate writes."
            )
        if self.metric == "duration_ms" and self.direction == "above":
            return (
                "The workflow took far longer than normal to complete. Check whether an "
                "upstream API is rate limiting or degrading, whether record volume grew, "
                "and whether the run is approaching its execution timeout — a workflow "
                "drifting toward its limit will start failing outright without further "
                "warning."
            )
        return (
            "The workflow completed much faster than normal. Combined with a normal "
            "record count this is usually harmless, but a fast run that also processed "
            "few records typically means it exited early."
        )


async def _percentiles(
    session: AsyncSession,
    *,
    integration_id: uuid.UUID,
    workflow_id: str,
    metric: str,
    exclude_run_id: str,
    now: datetime,
) -> tuple[int, float, float] | None:
    """
    Return (sample_size, p10, p95) for a metric, or None when below MIN_SAMPLE_SIZE.

    The execution being evaluated is excluded from its own baseline. It was just
    written by the same polling pass, and letting an outlier contribute to the
    band it is being tested against pulls the threshold toward the outlier.
    """
    column = _METRIC_COLUMNS[metric]
    window_start = now - timedelta(days=BASELINE_WINDOW_DAYS)

    stmt = select(
        func.count().label("n"),
        func.percentile_cont(LOWER_PERCENTILE).within_group(cast(column, Float)).label("p10"),
        func.percentile_cont(UPPER_PERCENTILE).within_group(cast(column, Float)).label("p95"),
    ).where(
        and_(
            Execution.integration_id == integration_id,
            Execution.workflow_id == workflow_id,
            Execution.status == "success",
            Execution.deleted_at.is_(None),
            Execution.created_at >= window_start,
            Execution.platform_run_id != exclude_run_id,
            column.is_not(None),
        )
    )

    row = (await session.execute(stmt)).first()
    if row is None:
        return None

    sample_size = int(row.n or 0)
    if sample_size < MIN_SAMPLE_SIZE or row.p10 is None or row.p95 is None:
        return None

    return sample_size, float(row.p10), float(row.p95)


async def check_baseline_anomalies(
    session: AsyncSession,
    *,
    integration_id: uuid.UUID,
    workflow_id: str,
    platform_run_id: str,
    status: str,
    items_processed: int | None,
    duration_ms: int | None,
) -> list[BaselineAnomaly]:
    """
    Compare an execution's metrics against the workflow's rolling percentile band.

    Only successful runs are evaluated, and only successful runs form the
    baseline. A failed run's counts describe how far it got before failing, not
    how much work the workflow normally does — mixing them in would drag the
    band down until genuine collapses stopped registering as unusual.
    """
    if status != "success":
        return []

    observed_values = {
        "items_processed": items_processed,
        "duration_ms": duration_ms,
    }
    now = datetime.now(UTC)
    anomalies: list[BaselineAnomaly] = []

    for metric, observed in observed_values.items():
        if observed is None:
            continue

        stats = await _percentiles(
            session,
            integration_id=integration_id,
            workflow_id=workflow_id,
            metric=metric,
            exclude_run_id=platform_run_id,
            now=now,
        )
        if stats is None:
            continue

        sample_size, p10, p95 = stats

        if observed < p10:
            direction = "below"
        elif observed > p95:
            direction = "above"
        else:
            continue

        anomaly = BaselineAnomaly(
            workflow_id=workflow_id,
            metric=metric,
            direction=direction,
            observed=float(observed),
            p10=p10,
            p95=p95,
            sample_size=sample_size,
        )
        anomalies.append(anomaly)

        logger.info(
            "baseline.anomaly_detected",
            integration_id=str(integration_id),
            workflow_id=workflow_id,
            metric=metric,
            direction=direction,
            observed=observed,
            p10=p10,
            p95=p95,
            sample_size=sample_size,
        )

    return anomalies
