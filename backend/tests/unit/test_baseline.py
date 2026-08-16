"""
Unit tests for historical baseline anomaly detection — Phase 2 deliverable 9.

The detector's value is entirely in where it draws the line, so these tests
concentrate on the boundaries: the minimum sample size below which no baseline
exists, the strict inequalities at p10/p95, and the rule that only successful
runs are evaluated or contribute.

The database is faked at the session boundary, matching the convention in
tests/integration/test_polling_to_slack.py — the percentile arithmetic is
Postgres's job, not this module's.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.services.diagnostic.baseline import (
    BASELINE_WINDOW_DAYS,
    MIN_SAMPLE_SIZE,
    BaselineAnomaly,
    check_baseline_anomalies,
)


def stats_row(n: int, p10: float, p95: float) -> SimpleNamespace:
    """A row shaped like the percentile query's RETURNING columns."""
    return SimpleNamespace(n=n, p10=p10, p95=p95)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeSession:
    """Returns queued percentile rows, one per execute() call, recording each statement."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.execute_calls = 0
        self.statements = []

    async def execute(self, _stmt=None, *_a, **_kw):
        self.execute_calls += 1
        self.statements.append(_stmt)
        row = self._rows.pop(0) if self._rows else None
        return FakeResult(row)


def rendered_sql(stmt) -> str:
    """Compile a statement to literal Postgres SQL."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def check(session, *, status="success", items=None, duration=None, run_id="run-99"):
    """Call the detector with the boilerplate filled in."""
    return await check_baseline_anomalies(
        session,
        integration_id=uuid.uuid4(),
        workflow_id="wf_orders",
        platform_run_id=run_id,
        status=status,
        items_processed=items,
        duration_ms=duration,
    )


class TestNoBaselineYet:
    """Below a usable sample size the detector must stay silent."""

    @pytest.mark.asyncio
    async def test_sample_below_minimum_produces_no_anomaly(self):
        """
        A percentile over a handful of runs is noise. Firing on it would train
        customers to ignore the feed during their evaluation period.
        """
        session = FakeSession([stats_row(MIN_SAMPLE_SIZE - 1, 100.0, 200.0)])

        assert await check(session, items=0) == []

    @pytest.mark.asyncio
    async def test_exactly_the_minimum_sample_is_enough(self):
        session = FakeSession([stats_row(MIN_SAMPLE_SIZE, 100.0, 200.0)])

        anomalies = await check(session, items=0)

        assert len(anomalies) == 1

    @pytest.mark.asyncio
    async def test_null_percentiles_produce_no_anomaly(self):
        """Postgres returns NULL percentiles when no rows match the filter."""
        session = FakeSession([stats_row(50, None, None)])

        assert await check(session, items=0) == []

    @pytest.mark.asyncio
    async def test_missing_row_produces_no_anomaly(self):
        session = FakeSession([None])

        assert await check(session, items=0) == []


class TestOnlySuccessfulRunsAreEvaluated:
    @pytest.mark.parametrize("status", ["error", "running", "timeout", "silent_fail"])
    @pytest.mark.asyncio
    async def test_non_success_short_circuits_before_querying(self, status):
        """
        A failed run's counts describe how far it got before failing, not how
        much work the workflow normally does. It is neither evaluated nor
        queried — the early return means no database round trip at all.
        """
        session = FakeSession([stats_row(100, 500.0, 900.0)])

        anomalies = await check(session, status=status, items=0)

        assert anomalies == []
        assert session.execute_calls == 0


class TestBandBoundaries:
    """Strict inequalities: the band edges themselves are normal."""

    @pytest.mark.asyncio
    async def test_below_p10_flags_as_below(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])

        anomalies = await check(session, items=99)

        assert len(anomalies) == 1
        assert anomalies[0].direction == "below"
        assert anomalies[0].metric == "items_processed"
        assert anomalies[0].observed == 99.0

    @pytest.mark.asyncio
    async def test_above_p95_flags_as_above(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])

        anomalies = await check(session, items=901)

        assert len(anomalies) == 1
        assert anomalies[0].direction == "above"

    @pytest.mark.asyncio
    async def test_exactly_p10_is_not_an_anomaly(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])

        assert await check(session, items=100) == []

    @pytest.mark.asyncio
    async def test_exactly_p95_is_not_an_anomaly(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])

        assert await check(session, items=900) == []

    @pytest.mark.asyncio
    async def test_inside_the_band_is_not_an_anomaly(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])

        assert await check(session, items=500) == []

    @pytest.mark.asyncio
    async def test_zero_items_flags_when_baseline_is_nonzero(self):
        """The headline case: a workflow that always does work suddenly does none."""
        session = FakeSession([stats_row(200, 3800.0, 4200.0)])

        anomalies = await check(session, items=0)

        assert anomalies[0].direction == "below"
        assert anomalies[0].severity == "warning"


class TestMetricSelection:
    @pytest.mark.asyncio
    async def test_none_metrics_are_skipped_without_querying(self):
        session = FakeSession([])

        assert await check(session, items=None, duration=None) == []
        assert session.execute_calls == 0

    @pytest.mark.asyncio
    async def test_duration_spike_is_detected(self):
        # items_processed is None, so only the duration query runs.
        session = FakeSession([stats_row(50, 200.0, 5000.0)])

        anomalies = await check(session, items=None, duration=9000)

        assert len(anomalies) == 1
        assert anomalies[0].metric == "duration_ms"
        assert anomalies[0].direction == "above"
        assert anomalies[0].severity == "warning"

    @pytest.mark.asyncio
    async def test_both_metrics_can_flag_independently(self):
        """Queries run in declaration order: items_processed, then duration_ms."""
        session = FakeSession(
            [
                stats_row(50, 100.0, 900.0),  # items: 10 is below p10
                stats_row(50, 200.0, 5000.0),  # duration: 9000 is above p95
            ]
        )

        anomalies = await check(session, items=10, duration=9000)

        assert {(a.metric, a.direction) for a in anomalies} == {
            ("items_processed", "below"),
            ("duration_ms", "above"),
        }


class TestSelfExclusion:
    @pytest.mark.asyncio
    async def test_execution_is_excluded_from_its_own_baseline(self):
        """
        The run under evaluation was written by the same polling pass. Letting an
        outlier contribute to the band it is tested against drags the threshold
        toward the outlier and masks the anomaly.
        """
        with patch(
            "app.services.diagnostic.baseline._percentiles",
            new=AsyncMock(return_value=(50, 100.0, 900.0)),
        ) as mock_percentiles:
            await check(FakeSession([]), items=10, run_id="run-under-test")

        assert mock_percentiles.await_args.kwargs["exclude_run_id"] == "run-under-test"


class TestQueryShape:
    """
    The tests above fake the session, so the query itself never reaches a
    database. These compile the statement the production code actually built and
    assert on the SQL — otherwise a percentile query that does not parse would
    pass every test in this file and fail only in production.
    """

    @pytest.fixture
    async def sql(self):
        session = FakeSession([stats_row(50, 100.0, 900.0)])
        await check(session, items=500, run_id="run-under-test")
        return rendered_sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_renders_postgres_percentile_syntax(self, sql):
        """percentile_cont is an ordered-set aggregate; it needs WITHIN GROUP."""
        assert "percentile_cont(0.1) WITHIN GROUP (ORDER BY" in sql
        assert "percentile_cont(0.95) WITHIN GROUP (ORDER BY" in sql

    @pytest.mark.asyncio
    async def test_baseline_is_built_from_successful_runs_only(self, sql):
        assert "executions.status = 'success'" in sql

    @pytest.mark.asyncio
    async def test_excludes_the_run_under_evaluation(self, sql):
        assert "executions.platform_run_id != 'run-under-test'" in sql

    @pytest.mark.asyncio
    async def test_excludes_soft_deleted_rows(self, sql):
        assert "executions.deleted_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_ignores_rows_with_a_null_metric(self, sql):
        """A NULL would otherwise be ordered into the percentile calculation."""
        assert "executions.items_processed IS NOT NULL" in sql

    @pytest.mark.asyncio
    async def test_bounds_the_window(self, sql):
        assert "executions.created_at >=" in sql


class TestSeverityRouting:
    """
    Severity is the noise control. Channels default to min_severity="warning",
    so info-level deviations land in the dashboard without paging anyone.
    """

    @pytest.mark.parametrize(
        ("metric", "direction", "expected"),
        [
            ("items_processed", "below", "warning"),  # volume collapse — the wedge
            ("items_processed", "above", "info"),
            ("duration_ms", "above", "warning"),  # upstream degrading
            ("duration_ms", "below", "info"),
        ],
    )
    def test_severity_matrix(self, metric, direction, expected):
        anomaly = BaselineAnomaly(
            workflow_id="wf",
            metric=metric,
            direction=direction,
            observed=1.0,
            p10=10.0,
            p95=90.0,
            sample_size=50,
        )

        assert anomaly.severity == expected


class TestAlertText:
    """An anomaly alert has to say what was expected and what arrived."""

    @pytest.fixture
    def collapse(self):
        return BaselineAnomaly(
            workflow_id="wf",
            metric="items_processed",
            direction="below",
            observed=3.0,
            p10=3800.0,
            p95=4200.0,
            sample_size=204,
        )

    def test_threshold_picks_the_relevant_percentile(self, collapse):
        assert collapse.threshold == 3800.0

        spike = BaselineAnomaly(
            workflow_id="wf",
            metric="duration_ms",
            direction="above",
            observed=9000.0,
            p10=200.0,
            p95=5000.0,
            sample_size=50,
        )
        assert spike.threshold == 5000.0

    def test_describe_states_observed_threshold_and_sample_size(self, collapse):
        described = collapse.describe()

        assert "3" in described
        assert "3800" in described
        assert "204" in described
        assert str(BASELINE_WINDOW_DAYS) in described

    def test_volume_collapse_fix_names_it_a_silent_failure(self, collapse):
        """
        This is the product's core thesis. If the remediation text does not tell
        the user to treat a successful-but-empty run as a probable failure, the
        alert has not done its job.
        """
        assert "silent failure" in collapse.suggested_fix().lower()

    def test_duration_spike_fix_warns_about_approaching_timeout(self):
        spike = BaselineAnomaly(
            workflow_id="wf",
            metric="duration_ms",
            direction="above",
            observed=9000.0,
            p10=200.0,
            p95=5000.0,
            sample_size=50,
        )

        assert "timeout" in spike.suggested_fix().lower()

    def test_volume_spike_fix_warns_about_duplicate_writes(self):
        spike = BaselineAnomaly(
            workflow_id="wf",
            metric="items_processed",
            direction="above",
            observed=90000.0,
            p10=100.0,
            p95=900.0,
            sample_size=50,
        )

        assert "duplicate" in spike.suggested_fix().lower()
