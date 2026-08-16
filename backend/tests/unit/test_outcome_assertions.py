"""
Unit tests for outcome assertion monitors — Phase 2 deliverable 6.

The value of an assertion is entirely in where it draws the line, so these
concentrate on boundaries and on the ways an assertion can fail *open*: an
unknown assertion name that gets skipped, a field that exists but is null, a
monitor with no assertions configured. Every one of those produces a monitor
that looks configured in the UI and can never fire, which is worse than no
monitor at all.

The database is faked at the session boundary, matching the convention in
test_baseline.py.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.models.monitor import Monitor
from app.schemas.monitor import MonitorCreate
from app.services.diagnostic.outcome import (
    OutcomeFailure,
    OutcomeViolation,
    check_outcome_assertions,
    evaluate_assertions,
)


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalars(self._rows)


class FakeSession:
    def __init__(self, monitors=()):
        self._monitors = list(monitors)
        self.execute_calls = 0

    async def execute(self, _stmt=None, *_a, **_kw):
        self.execute_calls += 1
        return FakeResult(self._monitors)


def make_monitor(expected_outcome, *, name="Orders must land", workflow_id="wf_orders"):
    return Monitor(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        workflow_id=workflow_id,
        name=name,
        monitor_type="outcome",
        expected_outcome=expected_outcome,
        alert_on_miss=True,
    )


def n8n_payload(items: list[dict]) -> dict:
    """A raw payload whose last node emitted these items."""
    return {
        "data": {
            "resultData": {
                "lastNodeExecuted": "Write to Sheet",
                "runData": {
                    "Write to Sheet": [{"data": {"main": [[{"json": item} for item in items]]}}]
                },
            }
        }
    }


async def check(session, *, status="success", items=None, items_processed=None):
    return await check_outcome_assertions(
        session,
        workspace_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        platform="n8n",
        workflow_id="wf_orders",
        status=status,
        items_processed=items_processed,
        raw_payload=n8n_payload(items or []),
    )


class TestMinRecords:
    @pytest.mark.parametrize(
        ("threshold", "count", "expected_failures"),
        [
            (1, 0, 1),  # the headline case: success with nothing written
            (1, 1, 0),  # exactly the threshold passes
            (5, 4, 1),
            (5, 5, 0),
            (5, 6, 0),
        ],
    )
    def test_boundary_is_at_least_not_more_than(self, threshold, count, expected_failures):
        items = [{"id": i} for i in range(count)]

        failures = evaluate_assertions({"min_records": threshold}, items, None)

        assert len(failures) == expected_failures

    def test_falls_back_to_items_processed_when_payload_has_no_items(self):
        """
        Without this fallback the assertion passes on every platform lacking an
        output extractor — a monitor that cannot fail.
        """
        assert evaluate_assertions({"min_records": 10}, [], 3)[0].assertion == "min_records"
        assert evaluate_assertions({"min_records": 10}, [], 50) == []

    def test_missing_items_processed_counts_as_zero(self):
        assert len(evaluate_assertions({"min_records": 1}, [], None)) == 1

    def test_non_integer_threshold_is_reported_not_ignored(self):
        failures = evaluate_assertions({"min_records": "five"}, [{"id": 1}], None)

        assert len(failures) == 1
        assert "integer" in failures[0].detail

    def test_bool_is_not_accepted_as_an_integer(self):
        """True == 1 in Python; accepting it would silently assert min_records=1."""
        failures = evaluate_assertions({"min_records": True}, [], None)

        assert len(failures) == 1
        assert "integer" in failures[0].detail


class TestFieldExists:
    def test_passes_when_every_item_has_the_field(self):
        items = [{"order_id": "a"}, {"order_id": "b"}]

        assert evaluate_assertions({"field_exists": "order_id"}, items, None) == []

    def test_fails_when_any_item_is_missing_it(self):
        items = [{"order_id": "a"}, {"other": "b"}]

        failures = evaluate_assertions({"field_exists": "order_id"}, items, None)

        assert len(failures) == 1
        assert "1 of 2" in failures[0].detail

    def test_a_present_but_null_field_still_counts_as_present(self):
        """
        field_exists asserts presence, not truthiness. A workflow explicitly
        writing null has produced the field; whether null is acceptable is what
        value_range is for.
        """
        assert evaluate_assertions({"field_exists": "note"}, [{"note": None}], None) == []

    def test_nested_paths_resolve(self):
        items = [{"customer": {"email": "a@b.test"}}]

        assert evaluate_assertions({"field_exists": "customer.email"}, items, None) == []
        assert len(evaluate_assertions({"field_exists": "customer.phone"}, items, None)) == 1

    def test_no_output_items_fails_rather_than_passing_vacuously(self):
        """
        "Every item has the field" is trivially true of zero items. Passing there
        would make the assertion silent on exactly the run worth alerting on.
        """
        failures = evaluate_assertions({"field_exists": "order_id"}, [], None)

        assert len(failures) == 1
        assert "no output items" in failures[0].detail

    def test_a_list_of_fields_is_accepted(self):
        items = [{"a": 1, "b": 2}]

        assert evaluate_assertions({"field_exists": ["a", "b"]}, items, None) == []
        assert len(evaluate_assertions({"field_exists": ["a", "c"]}, items, None)) == 1


class TestValueRange:
    @pytest.mark.parametrize(
        ("value", "expected_failures"),
        [(-1, 1), (0, 0), (50, 0), (100, 0), (101, 1)],
    )
    def test_bounds_are_inclusive(self, value, expected_failures):
        config = {"value_range": {"field": "total", "min": 0, "max": 100}}

        failures = evaluate_assertions(config, [{"total": value}], None)

        assert len(failures) == expected_failures

    def test_one_sided_range_is_allowed(self):
        config = {"value_range": {"field": "total", "min": 0}}

        assert evaluate_assertions(config, [{"total": 999999}], None) == []
        assert len(evaluate_assertions(config, [{"total": -1}], None)) == 1

    def test_a_range_with_no_bounds_is_reported_as_misconfigured(self):
        failures = evaluate_assertions({"value_range": {"field": "total"}}, [{"total": 1}], None)

        assert len(failures) == 1
        assert "neither min nor max" in failures[0].detail

    def test_non_numeric_value_is_reported_not_skipped(self):
        config = {"value_range": {"field": "total", "min": 0}}

        failures = evaluate_assertions(config, [{"total": "twelve"}], None)

        assert len(failures) == 1
        assert "not numeric" in failures[0].detail

    def test_bool_is_not_treated_as_numeric(self):
        """True < 0 is False, so a bool would silently satisfy any minimum."""
        config = {"value_range": {"field": "flag", "min": 5}}

        failures = evaluate_assertions(config, [{"flag": True}], None)

        assert len(failures) == 1
        assert "not numeric" in failures[0].detail

    def test_missing_field_is_reported(self):
        config = {"value_range": {"field": "total", "min": 0}}

        failures = evaluate_assertions(config, [{"other": 1}], None)

        assert len(failures) == 1
        assert "missing" in failures[0].detail

    def test_every_item_is_checked_not_just_the_first(self):
        config = {"value_range": {"field": "total", "max": 10}}

        failures = evaluate_assertions(config, [{"total": 1}, {"total": 99}], None)

        assert len(failures) == 1


class TestMisconfiguration:
    def test_unknown_assertion_fails_rather_than_being_skipped(self):
        """
        A typo'd assertion name that is silently ignored produces a monitor that
        can never fire. Reporting it makes the mistake visible immediately.
        """
        failures = evaluate_assertions({"min_recrods": 5}, [], None)

        assert len(failures) == 1
        assert "unknown assertion" in failures[0].detail

    def test_valid_assertions_still_evaluate_alongside_an_unknown_one(self):
        failures = evaluate_assertions({"bogus": 1, "min_records": 5}, [], None)

        assert {f.assertion for f in failures} == {"bogus", "min_records"}

    def test_empty_config_produces_no_failures(self):
        assert evaluate_assertions({}, [], None) == []


class TestAllMustPass:
    def test_every_failing_assertion_is_reported_not_just_the_first(self):
        """
        One alert naming all three beats three alerts, and beats a whack-a-mole
        where fixing one reveals the next on the following poll.
        """
        config = {
            "min_records": 10,
            "field_exists": "missing_field",
            "value_range": {"field": "total", "max": 5},
        }

        failures = evaluate_assertions(config, [{"total": 99}], None)

        assert len(failures) == 3

    def test_all_passing_yields_nothing(self):
        config = {
            "min_records": 1,
            "field_exists": "total",
            "value_range": {"field": "total", "min": 0, "max": 100},
        }

        assert evaluate_assertions(config, [{"total": 50}], None) == []


class TestMonitorSelection:
    @pytest.mark.asyncio
    async def test_failed_runs_are_not_evaluated_or_queried(self):
        """
        A failed run's output is incomplete by definition. Asserting against it
        would raise an outcome alert on top of the rule engine's error alert —
        two alerts for one problem.
        """
        session = FakeSession([make_monitor({"min_records": 1})])

        assert await check(session, status="error") == []
        assert session.execute_calls == 0

    @pytest.mark.asyncio
    async def test_no_monitors_means_no_violations(self):
        assert await check(FakeSession([]), items=[]) == []

    @pytest.mark.asyncio
    async def test_violation_carries_the_monitor_identity(self):
        monitor = make_monitor({"min_records": 5}, name="Orders must land")
        session = FakeSession([monitor])

        violations = await check(session, items=[])

        assert len(violations) == 1
        assert violations[0].monitor_id == monitor.id
        assert violations[0].monitor_name == "Orders must land"

    @pytest.mark.asyncio
    async def test_a_monitor_with_no_assertions_is_skipped_not_crashed(self):
        session = FakeSession([make_monitor(None)])

        assert await check(session, items=[]) == []

    @pytest.mark.asyncio
    async def test_each_monitor_is_evaluated_independently(self):
        session = FakeSession(
            [
                make_monitor({"min_records": 1}, name="passes"),
                make_monitor({"min_records": 99}, name="fails"),
            ]
        )

        violations = await check(session, items=[{"id": 1}])

        assert [v.monitor_name for v in violations] == ["fails"]

    @pytest.mark.asyncio
    async def test_items_are_extracted_from_the_real_payload_shape(self):
        """Guards the n8n path this depends on, shared with schema drift."""
        session = FakeSession([make_monitor({"field_exists": "order_id"})])

        assert await check(session, items=[{"order_id": "a"}]) == []


class TestAlertText:
    def test_single_failure_reads_as_one_problem(self):
        violation = OutcomeViolation(
            monitor_id=uuid.uuid4(),
            monitor_name="Orders",
            workflow_id="wf",
            failures=[OutcomeFailure("min_records", "expected at least 1 record(s), found 0")],
        )

        assert violation.describe().startswith("Outcome assertion failed:")
        assert "found 0" in violation.describe()

    def test_multiple_failures_are_counted_and_listed(self):
        violation = OutcomeViolation(
            monitor_id=uuid.uuid4(),
            monitor_name="Orders",
            workflow_id="wf",
            failures=[OutcomeFailure("a", "first"), OutcomeFailure("b", "second")],
        )

        assert "2 outcome assertions failed" in violation.describe()
        assert "first" in violation.describe()
        assert "second" in violation.describe()

    def test_severity_is_critical(self):
        """A stated assertion breaking is a broken promise, not a statistical hint."""
        violation = OutcomeViolation(monitor_id=uuid.uuid4(), monitor_name="m", workflow_id="wf")

        assert violation.severity == "critical"

    def test_fix_names_the_monitor_and_the_silent_nature_of_the_failure(self):
        violation = OutcomeViolation(
            monitor_id=uuid.uuid4(), monitor_name="Orders must land", workflow_id="wf"
        )
        fix = violation.suggested_fix()

        assert "Orders must land" in fix
        assert "reported success" in fix


class TestCreationValidation:
    def test_outcome_monitor_without_workflow_id_is_rejected(self):
        """
        It would match no execution and watch nothing, while appearing correctly
        configured. See DECISIONS.md — this is why workflow_id is a column.
        """
        with pytest.raises(ValidationError, match="workflow_id is required"):
            MonitorCreate(name="Orders", monitor_type="outcome")

    def test_outcome_monitor_with_workflow_id_is_accepted(self):
        monitor = MonitorCreate(name="Orders", monitor_type="outcome", workflow_id="wf_orders")

        assert monitor.workflow_id == "wf_orders"

    @pytest.mark.parametrize("monitor_type", ["heartbeat", "cron"])
    def test_ping_driven_monitors_still_need_no_workflow(self, monitor_type):
        """NULL is the semantically correct value for these, not a placeholder."""
        monitor = MonitorCreate(name="Nightly", monitor_type=monitor_type)

        assert monitor.workflow_id is None
