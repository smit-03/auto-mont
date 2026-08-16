"""
Unit tests for schema drift detection — Phase 2 deliverable 4.

Covers the three drift kinds named in the Blueprint (field addition, field
removal, type change) plus the payload extraction and null-handling decisions
the detector rests on.
"""

import pytest

from app.services.diagnostic.schema_checker import (
    build_drift_fix,
    build_field_map,
    compute_fingerprint,
    diff_field_maps,
    extract_output_items,
)


def n8n_payload(items: list[dict], last_node: str = "Postgres") -> dict:
    """Build an n8n execution payload whose final node emitted `items`."""
    return {
        "id": "exec_1",
        "status": "success",
        "data": {
            "resultData": {
                "lastNodeExecuted": last_node,
                "runData": {
                    "Webhook": [
                        {"data": {"main": [[{"json": {"raw": "upstream"}}]]}},
                    ],
                    last_node: [
                        {"data": {"main": [[{"json": item} for item in items]]}},
                    ],
                },
            }
        },
    }


class TestFieldMap:
    """Flattening execution output into {path: type}."""

    def test_flattens_nested_objects_to_dotted_paths(self):
        items = [{"id": 1, "customer": {"email": "a@b.c", "vip": True}}]

        field_map = build_field_map(items)

        assert field_map["id"] == "number"
        assert field_map["customer"] == "object"
        assert field_map["customer.email"] == "string"
        assert field_map["customer.vip"] == "boolean"

    def test_booleans_are_not_reported_as_numbers(self):
        """bool subclasses int in Python; the type check must order accordingly."""
        field_map = build_field_map([{"flag": True, "count": 1}])

        assert field_map["flag"] == "boolean"
        assert field_map["count"] == "number"

    def test_concrete_type_beats_null_across_items(self):
        """A field null in one item and populated in another is a nullable field."""
        items = [{"discount": None}, {"discount": 12.5}]

        field_map = build_field_map(items)

        assert field_map["discount"] == "number"

    def test_field_null_in_every_item_is_typed_null(self):
        field_map = build_field_map([{"discount": None}, {"discount": None}])

        assert field_map["discount"] == "null"

    def test_union_of_fields_across_items(self):
        """A path present in any item belongs in the map."""
        field_map = build_field_map([{"a": 1}, {"b": "x"}])

        assert set(field_map) == {"a", "b"}

    def test_non_dict_items_are_skipped(self):
        field_map = build_field_map([{"a": 1}, "not a dict", None])

        assert field_map == {"a": "number"}


class TestFingerprint:
    """The fingerprint is the cheap equality check before any diffing."""

    def test_is_order_independent(self):
        a = compute_fingerprint({"x": "string", "y": "number"})
        b = compute_fingerprint({"y": "number", "x": "string"})

        assert a == b

    def test_changes_when_a_field_is_added(self):
        before = compute_fingerprint({"x": "string"})
        after = compute_fingerprint({"x": "string", "y": "number"})

        assert before != after

    def test_changes_when_a_type_changes(self):
        before = compute_fingerprint({"x": "string"})
        after = compute_fingerprint({"x": "number"})

        assert before != after


class TestDiff:
    """Blueprint deliverable 4: field addition / removal / type change."""

    def test_detects_field_addition(self):
        drift = diff_field_maps({"id": "number"}, {"id": "number", "email": "string"}, "wf")

        assert drift.added == {"email": "string"}
        assert not drift.removed
        assert not drift.type_changed
        assert not drift.is_empty

    def test_detects_field_removal(self):
        drift = diff_field_maps({"id": "number", "email": "string"}, {"id": "number"}, "wf")

        assert drift.removed == {"email": "string"}
        assert not drift.added

    def test_detects_type_change(self):
        drift = diff_field_maps({"total": "number"}, {"total": "string"}, "wf")

        assert drift.type_changed == {"total": ("number", "string")}

    def test_detects_a_rename_as_removal_plus_addition(self):
        """The canonical silent failure: upstream renames customer_email to email."""
        drift = diff_field_maps(
            {"id": "number", "customer_email": "string"},
            {"id": "number", "email": "string"},
            "wf",
        )

        assert drift.removed == {"customer_email": "string"}
        assert drift.added == {"email": "string"}
        assert drift.severity == "critical"

    def test_identical_maps_produce_no_drift(self):
        drift = diff_field_maps({"id": "number"}, {"id": "number"}, "wf")

        assert drift.is_empty

    @pytest.mark.parametrize(
        ("before", "after"),
        [("null", "string"), ("string", "null"), ("null", "number")],
    )
    def test_null_transitions_are_not_type_changes(self, before, after):
        """
        A nullable field being populated or omitted is ordinary. Reporting it
        would fire on essentially every workflow that has an optional field.
        """
        drift = diff_field_maps({"discount": before}, {"discount": after}, "wf")

        assert not drift.type_changed
        assert drift.is_empty


class TestDriftSeverity:
    """Additions are informational; removals and type changes break consumers."""

    def test_addition_only_is_info(self):
        drift = diff_field_maps({"id": "number"}, {"id": "number", "new": "string"}, "wf")

        assert drift.severity == "info"

    def test_removal_is_critical(self):
        drift = diff_field_maps({"id": "number", "gone": "string"}, {"id": "number"}, "wf")

        assert drift.severity == "critical"

    def test_type_change_is_critical(self):
        drift = diff_field_maps({"total": "number"}, {"total": "string"}, "wf")

        assert drift.severity == "critical"


class TestDescribeAndFix:
    """Alert bodies must name the actual fields that moved."""

    def test_describe_names_added_and_removed_fields(self):
        drift = diff_field_maps(
            {"id": "number", "customer_email": "string"},
            {"id": "number", "email": "string"},
            "wf",
        )

        described = drift.describe()

        assert "customer_email" in described
        assert "email" in described

    def test_describe_shows_both_sides_of_a_type_change(self):
        drift = diff_field_maps({"total": "number"}, {"total": "string"}, "wf")

        described = drift.describe()

        assert "number" in described
        assert "string" in described

    def test_removal_fix_warns_about_silent_nulls(self):
        drift = diff_field_maps({"id": "number", "gone": "string"}, {"id": "number"}, "wf")

        assert "null" in build_drift_fix(drift).lower()

    def test_addition_only_fix_says_no_action_needed(self):
        drift = diff_field_maps({"id": "number"}, {"id": "number", "new": "string"}, "wf")

        assert "no action" in build_drift_fix(drift).lower()


class TestN8NExtraction:
    """Pulling the final node's output items out of an n8n payload."""

    def test_extracts_items_from_last_node_executed(self):
        items = extract_output_items("n8n", n8n_payload([{"id": 1}, {"id": 2}]))

        assert items == [{"id": 1}, {"id": 2}]

    def test_ignores_intermediate_node_output(self):
        """
        Fingerprinting every node would report any internal change as drift.
        The Webhook node's {"raw": ...} field must not appear.
        """
        items = extract_output_items("n8n", n8n_payload([{"id": 1}]))

        assert all("raw" not in item for item in items)

    def test_falls_back_to_last_run_node_when_marker_absent(self):
        payload = n8n_payload([{"id": 1}])
        del payload["data"]["resultData"]["lastNodeExecuted"]

        items = extract_output_items("n8n", payload)

        assert items == [{"id": 1}]

    def test_unknown_platform_yields_no_items(self):
        """Disables drift detection rather than fingerprinting the wrong thing."""
        assert extract_output_items("make", n8n_payload([{"id": 1}])) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"data": None},
            {"data": {}},
            {"data": {"resultData": {}}},
            {"data": {"resultData": {"runData": {}}}},
            {"data": {"resultData": {"runData": {"Node": "not a list"}}}},
        ],
    )
    def test_malformed_payloads_yield_no_items(self, payload):
        """Polling must never crash on an unexpected payload shape."""
        assert extract_output_items("n8n", payload) == []
