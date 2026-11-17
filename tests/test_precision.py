"""Offline precision evidence tests. All documented-grid rules here are synthetic."""
from decimal import Decimal, localcontext
import json
import math
from pathlib import Path
import socket

import pytest

from telecom_pipeline.incremental import METRIC_COLUMNS
from telecom_pipeline.precision import DocumentedPrecision, assess_metric, profile_source
from telecom_pipeline import snowflake_config


METRIC = METRIC_COLUMNS[0]
SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Precision assessment attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)


def synthetic_rule(metric=METRIC, places=2):
    return DocumentedPrecision(metric, places, "Synthetic test specification only", "test-reviewer")


@pytest.mark.parametrize("metric", METRIC_COLUMNS)
def test_exact_canonical_equality_for_each_metric(metric):
    result = assess_metric(metric, "1.2500", 1.25, accepted_source="1.25")
    assert result["source_change"] == "UNCHANGED"
    assert result["readback"] == "MATCH"
    assert result["reference_exactly_representable"]
    assert not result["requires_review"]


def test_decimal_string_match_does_not_recover_source_precision():
    result = assess_metric(METRIC, "0.1", 0.1)
    assert result["source_change"] == "UNRESOLVED"
    assert result["observed_shortest_decimal"] == "0.1"
    assert Decimal(result["observed_exact_binary_decimal"]) != Decimal("0.1")
    assert result["requires_review"]
    assert result["documented_decimal_places"] is None


def test_known_canonical_decimal_distinguishes_binary_representation_error():
    result = assess_metric(METRIC, "0.100", 0.1, accepted_source="0.1")
    assert result["source_change"] == "UNCHANGED"
    assert result["readback"] == "MATCH"
    assert not result["reference_exactly_representable"]
    assert result["ulp_distance_from_projection"] == "0"


def test_meaningful_revision_compares_against_accepted_value():
    result = assess_metric(METRIC, "100.01", 100.0, accepted_source="100.00")
    assert result["source_change"] == "REVISED"
    assert result["readback"] == "MATCH"
    assert result["projection_reference"] == "ACCEPTED_SOURCE"


def test_source_revision_survives_float_collision():
    assert float(Decimal("0.100000000000000005")) == 0.1
    result = assess_metric(METRIC, "0.100000000000000005", 0.1, accepted_source="0.1")
    assert result["source_change"] == "REVISED"
    assert result["readback"] == "MATCH"


@pytest.mark.parametrize("incoming,observed,accepted,change,readback", [
    (None, None, None, "UNCHANGED", "NULL_MATCH"),
    ("0", 0.0, "0", "UNCHANGED", "MATCH"),
    ("0", None, None, "REVISED", "NULL_MATCH"),
    (None, 0.0, "0", "REVISED", "MATCH"),
    (None, 0.0, None, "UNCHANGED", "NULL_MISMATCH"),
    ("1", None, "1", "UNCHANGED", "NULL_MISMATCH"),
])
def test_null_zero_and_warehouse_consistency_are_independent(incoming, observed, accepted, change, readback):
    result = assess_metric(METRIC, incoming, observed, accepted_source=accepted)
    assert result["source_change"] == change
    assert result["readback"] == readback


@pytest.mark.parametrize("incoming,observed,expected", [(None, None, "UNCHANGED"),
    ("0", None, "REVISED"), (None, 0.0, "REVISED"), ("0", 0.0, "UNRESOLVED")])
def test_null_transitions_without_canonical_accepted_evidence(incoming, observed, expected):
    assert assess_metric(METRIC, incoming, observed)["source_change"] == expected


def test_single_ulp_disagreement_is_reported_without_a_tolerance():
    result = assess_metric(METRIC, "1", math.nextafter(1.0, math.inf), accepted_source="1")
    assert result["readback"] == "MISMATCH"
    assert result["ulp_distance_from_projection"] == "1"
    assert result["requires_review"]


@pytest.mark.parametrize("incoming,observed,expected", [("1e400", 1.0, "OVERFLOW"),
                                                      ("1e-400", 0.0, "UNDERFLOW")])
def test_binary64_range_loss_requires_review(incoming, observed, expected):
    value = Decimal(incoming)
    result = assess_metric(METRIC, value, observed, accepted_source=value)
    assert result["readback"] == expected
    assert result["requires_review"]


def test_documented_grid_can_identify_unique_accepted_value():
    result = assess_metric(METRIC, "0.10", 0.1, precision=synthetic_rule())
    assert result["source_change"] == "UNCHANGED"
    assert result["accepted_evidence"] == "DOCUMENTED_GRID"
    assert result["recovered_grid_value"] == "0.10"
    assert result["precision_evidence"].startswith("Synthetic")
    assert result["publication_allowed"] is False


def test_documented_grid_detects_a_revision():
    result = assess_metric(METRIC, "0.11", 0.1, precision=synthetic_rule())
    assert result["source_change"] == "REVISED"


def test_documented_grid_does_not_round_off_grid_input():
    with pytest.raises(ValueError, match="violates"):
        assess_metric(METRIC, "0.101", 0.1, precision=synthetic_rule())


def test_precision_rule_cannot_be_applied_to_another_metric():
    with pytest.raises(ValueError, match="different metric"):
        assess_metric(METRIC_COLUMNS[1], "0.1", 0.1, precision=synthetic_rule())


def test_integer_grid_boundary_collision_remains_unresolved():
    assert float(9007199254740993) == float(9007199254740992)
    result = assess_metric(METRIC, "9007199254740992", float(9007199254740992),
                           precision=synthetic_rule(places=0))
    assert result["source_change"] == "UNRESOLVED"
    assert result["accepted_evidence"] == "UNAVAILABLE"


def test_zero_with_tiny_grid_is_ambiguous_and_preserves_underflow():
    result = assess_metric(METRIC, Decimal("1e-400"), 0.0, precision=synthetic_rule(places=400))
    assert result["source_change"] == "UNRESOLVED"
    assert result["readback"] == "UNDERFLOW"


def test_non_grid_warehouse_value_remains_unresolved():
    result = assess_metric(METRIC, "0.10", 0.105, precision=synthetic_rule())
    assert result["source_change"] == "UNRESOLVED"


def test_assessment_is_deterministic_across_decimal_contexts():
    first = assess_metric(METRIC, "0.10", 0.1, precision=synthetic_rule())
    with localcontext() as context:
        context.prec = 3
        second = assess_metric(METRIC, "0.10", 0.1, precision=synthetic_rule())
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("observed", [float("inf"), float("nan"), -0.1, 1, True, Decimal("0.1")])
def test_invalid_float_values_rejected(observed):
    with pytest.raises(ValueError, match="finite nonnegative"):
        assess_metric(METRIC, "0.1", observed)


def test_original_snapshot_profile_is_observation_only():
    before = SOURCE.read_bytes()
    report = profile_source(SOURCE)
    assert report["row_count"] == 66
    assert report["metrics"][METRIC]["observed_decimal_places"] == {2: 66}
    assert report["metrics"]["FIXED_LOCAL_MIN_M"]["observed_decimal_places"] == {6: 51}
    assert report["metrics"]["INTERNET_CAPACITY_MBPS"]["observed_decimal_places"] == {0: 65}
    assert [report["metrics"][metric]["null_count"] for metric in METRIC_COLUMNS] == [0, 0, 4, 15, 32, 32, 1]
    assert all(rule["documented_decimal_places"] is None for rule in report["metrics"].values())
    assert all(rule["warehouse_type"] == "FLOAT" for rule in report["metrics"].values())
    assert profile_source(SOURCE) == report
    assert SOURCE.read_bytes() == before
