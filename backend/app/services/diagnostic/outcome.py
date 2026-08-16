"""
Outcome assertion monitors — Phase 2 deliverable 6.

The premise of this product is that a workflow reporting success is not the same
as a workflow doing its job. Schema drift catches a changed output *shape*;
baseline anomaly detection catches a changed output *volume* relative to
history. Outcome assertions catch the case neither can: output that is the wrong
*content* against a rule the customer stated up front, with no history needed.

Assertions are evaluated inline in the polling path rather than on the heartbeat
beat tick, so every ingested run is checked rather than whichever run happened
to be latest when the tick fired. See DECISIONS.md for that tradeoff and the
known gap it leaves in ``GET /monitors/{id}/status``.

Output items come from ``extract_output_items`` — the same extractor schema
drift uses, so "the workflow's output" means one thing across both features.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.monitor import Monitor
from app.services.diagnostic.schema_checker import extract_output_items

logger = get_logger(__name__)

# Assertion kinds this engine understands. A monitor configured with anything
# else is reported as misconfigured rather than silently passing — a monitor
# that cannot fail is indistinguishable from one that is watching nothing.
SUPPORTED_ASSERTIONS = ("min_records", "field_exists", "value_range")


@dataclass
class OutcomeFailure:
    """One assertion that did not hold."""

    assertion: str
    detail: str


@dataclass
class OutcomeViolation:
    """
    The result of evaluating one monitor against one execution.

    Carries every failed assertion rather than the first, so a run that is wrong
    in three ways produces one alert naming all three instead of three alerts or
    a game of whack-a-mole across three polls.
    """

    monitor_id: uuid.UUID
    monitor_name: str
    workflow_id: str
    failures: list[OutcomeFailure] = field(default_factory=list)

    @property
    def severity(self) -> str:
        # An explicit customer-stated assertion that no longer holds is a
        # concrete broken promise, not a statistical hint like a baseline
        # deviation. It pages.
        return "critical"

    def describe(self) -> str:
        if len(self.failures) == 1:
            return f"Outcome assertion failed: {self.failures[0].detail}"
        joined = "; ".join(f.detail for f in self.failures)
        return f"{len(self.failures)} outcome assertions failed: {joined}"

    def suggested_fix(self) -> str:
        return (
            f"The workflow reported success, but its output violates the "
            f"assertions configured on monitor '{self.monitor_name}'. Because "
            f"the run did not error, nothing else would have surfaced this. "
            f"Check the workflow's final node against what it is expected to "
            f"produce, then confirm whether the workflow or the assertion is "
            f"the thing that is out of date."
        )


def _get_path(item: dict, path: str) -> tuple[bool, Any]:
    """
    Resolve a dotted path within one output item.

    Returns (found, value) rather than a sentinel: ``None`` is a legitimate
    stored value, and conflating "absent" with "present and null" would make
    field_exists pass on a field the workflow explicitly nulled out.
    """
    current: Any = item
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _check_min_records(expected: Any, items: list[dict], items_processed: int | None) -> str | None:
    """
    Assert the run produced at least N records.

    Counts extracted output items, falling back to the normalized
    ``items_processed`` when the payload carries no extractable output (a
    platform without an extractor, or a workflow whose last node emits nothing
    structured). Without the fallback this assertion would silently pass on
    every platform but n8n.
    """
    if not isinstance(expected, int) or isinstance(expected, bool):
        return f"min_records must be an integer, got {expected!r}"

    observed = len(items) if items else (items_processed or 0)
    if observed < expected:
        return f"expected at least {expected} record(s), found {observed}"
    return None


def _check_field_exists(expected: Any, items: list[dict]) -> str | None:
    """Assert every output item carries the named field(s)."""
    paths = [expected] if isinstance(expected, str) else expected
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return f"field_exists must be a string or list of strings, got {expected!r}"

    if not items:
        return f"expected field(s) {', '.join(paths)} but the run produced no output items"

    for path in paths:
        missing = sum(1 for item in items if not _get_path(item, path)[0])
        if missing:
            return f"field '{path}' missing from {missing} of {len(items)} output item(s)"
    return None


def _check_value_range(expected: Any, items: list[dict]) -> str | None:
    """
    Assert a numeric field falls within [min, max] across all output items.

    Config shape: ``{"field": "total", "min": 0, "max": 10000}``. Both bounds
    are optional; omitting one makes the check one-sided.
    """
    if not isinstance(expected, dict):
        return f"value_range must be an object, got {expected!r}"

    path = expected.get("field")
    if not isinstance(path, str):
        return "value_range requires a 'field' naming the value to check"

    low, high = expected.get("min"), expected.get("max")
    if low is None and high is None:
        return f"value_range on '{path}' specifies neither min nor max"

    for item in items:
        found, value = _get_path(item, path)
        if not found:
            return f"field '{path}' missing, cannot range-check it"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"field '{path}' is {value!r}, which is not numeric"
        if low is not None and value < low:
            return f"'{path}' was {value}, below the minimum of {low}"
        if high is not None and value > high:
            return f"'{path}' was {value}, above the maximum of {high}"
    return None


def evaluate_assertions(
    expected_outcome: dict,
    items: list[dict],
    items_processed: int | None,
) -> list[OutcomeFailure]:
    """
    Run every configured assertion and return all failures.

    All assertions must pass. Unknown keys are reported as failures rather than
    ignored: a typo'd assertion name that is silently skipped produces a monitor
    that looks configured and can never fire, which is the failure mode the
    workflow_id column was also introduced to prevent.
    """
    failures: list[OutcomeFailure] = []

    for name, expected in expected_outcome.items():
        if name not in SUPPORTED_ASSERTIONS:
            failures.append(
                OutcomeFailure(
                    assertion=name,
                    detail=(
                        f"unknown assertion '{name}' — supported: {', '.join(SUPPORTED_ASSERTIONS)}"
                    ),
                )
            )
            continue

        if name == "min_records":
            detail = _check_min_records(expected, items, items_processed)
        elif name == "field_exists":
            detail = _check_field_exists(expected, items)
        else:
            detail = _check_value_range(expected, items)

        if detail:
            failures.append(OutcomeFailure(assertion=name, detail=detail))

    return failures


async def check_outcome_assertions(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    platform: str,
    workflow_id: str,
    status: str,
    items_processed: int | None,
    raw_payload: dict,
) -> list[OutcomeViolation]:
    """
    Evaluate every outcome monitor scoped to this workflow against one run.

    Only successful runs are evaluated. A failed run's output is incomplete by
    definition, so asserting against it would produce an outcome alert on top of
    the error alert the rule engine already raised for the same cause — two
    alerts, one problem.
    """
    if status != "success":
        return []

    result = await session.execute(
        select(Monitor).where(
            Monitor.integration_id == integration_id,
            Monitor.workflow_id == workflow_id,
            Monitor.monitor_type == "outcome",
            Monitor.deleted_at.is_(None),
            Monitor.alert_on_miss.is_(True),
        )
    )
    monitors = list(result.scalars().all())
    if not monitors:
        return []

    # Extracted once and shared: every monitor on this workflow asserts against
    # the same run's output.
    items = extract_output_items(platform, raw_payload)

    violations: list[OutcomeViolation] = []
    for monitor in monitors:
        if not monitor.expected_outcome:
            logger.warning(
                "outcome.monitor_has_no_assertions",
                monitor_id=str(monitor.id),
                workflow_id=workflow_id,
            )
            continue

        failures = evaluate_assertions(monitor.expected_outcome, items, items_processed)
        if failures:
            violations.append(
                OutcomeViolation(
                    monitor_id=monitor.id,
                    monitor_name=monitor.name,
                    workflow_id=workflow_id,
                    failures=failures,
                )
            )

    return violations
