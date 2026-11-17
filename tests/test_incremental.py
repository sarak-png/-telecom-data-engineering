"""Offline classifier tests. All constructed updates are synthetic engineering fixtures."""
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import pandas as pd
import pytest

from telecom_pipeline.incremental import (
    COLUMN_MAPPING, METRIC_COLUMNS, CanonicalRecord, Classification,
    InputMode, canonicalize_record, classify_records,
)
from telecom_pipeline.ingestion import load_tra_csv
from telecom_pipeline.snowflake_config import WAREHOUSE_CONTRACT
from telecom_pipeline.validation import ValidationError


@pytest.fixture
def observation():
    """A complete synthetic quarterly observation, unrelated to real TRA measurements."""
    def make(year=2026, quarter=2, **changes):
        row = {"YEAR": year, "QUARTER": quarter, **{name: "100.00" for name in METRIC_COLUMNS}}
        row.update(changes)
        return row
    return make


def test_new_quarter_delta(observation):
    plan = classify_records([observation(quarter=3)], [observation()],
                            input_mode=InputMode.QUARTER_DELTA, declared_periods=[(2026, 3)])
    assert plan.status == "READY"
    assert plan.counts == {"NEW": 1, "UNCHANGED": 0, "REVISED": 0, "CONFLICTING_DUPLICATE": 0}
    assert plan.records[0].before is None
    assert plan.records[0].after.key == (2026, 3)
    assert not plan.requires_approval


def test_unchanged_numeric_formatting_and_column_order(observation):
    incoming = observation(YEAR="2,026.00", QUARTER="2.00", **{name: " 100.0000 " for name in METRIC_COLUMNS})
    plan = classify_records([dict(reversed(list(incoming.items())))], [observation()], input_mode="FULL_SNAPSHOT")
    record = plan.records[0]
    assert record.classification == Classification.UNCHANGED
    assert record.changed_metrics == ()
    assert record.before.payload_hash == record.after.payload_hash


@pytest.mark.parametrize("metric", METRIC_COLUMNS)
def test_each_metric_is_compared_and_revision_waits(observation, metric):
    plan = classify_records([observation(**{metric: "100.01"})], [observation()], input_mode="FULL_SNAPSHOT")
    record = plan.records[0]
    assert plan.status == "WAITING_APPROVAL"
    assert plan.requires_approval
    assert record.classification == Classification.REVISED
    assert record.changed_metrics == (metric,)
    assert record.before.as_dict()[metric] == Decimal("100")
    assert record.after.as_dict()[metric] == Decimal("100.01")


@pytest.mark.parametrize("before,after", [(None, "12.5"), (None, "0"), ("12.5", None), ("0", None)])
def test_null_transitions_are_revisions(observation, before, after):
    metric = METRIC_COLUMNS[0]
    plan = classify_records([observation(**{metric: after})], [observation(**{metric: before})], input_mode="FULL_SNAPSHOT")
    assert plan.status == "WAITING_APPROVAL"
    assert plan.records[0].changed_metrics == (metric,)
    assert (plan.records[0].before.metrics[0] is None) == (before is None)
    assert (plan.records[0].after.metrics[0] is None) == (after is None)


def test_all_nulls_and_genuine_zero_are_preserved(observation):
    row = observation(**{name: None for name in METRIC_COLUMNS})
    plan = classify_records([row], [row], input_mode="FULL_SNAPSHOT")
    assert plan.records[0].after.metrics == (None,) * 7
    assert plan.records[0].classification == Classification.UNCHANGED
    zero = canonicalize_record(observation(**{METRIC_COLUMNS[0]: 0}))
    assert zero.metrics[0] == Decimal(0)
    assert zero.metrics[0] is not None


@pytest.mark.parametrize("null_value", [None, pd.NA, "", "   "])
def test_source_missing_cells_compare_as_null(observation, null_value):
    metric = METRIC_COLUMNS[0]
    plan = classify_records([observation(**{metric: null_value})], [observation(**{metric: None})], input_mode="FULL_SNAPSHOT")
    assert plan.records[0].classification == Classification.UNCHANGED
    assert plan.records[0].after.metrics[0] is None


def test_identical_duplicates_collapse_and_preserve_ordinals(observation):
    row, equivalent = observation(), observation(**{name: Decimal("100.000") for name in METRIC_COLUMNS})
    plan = classify_records([row, equivalent], input_mode="QUARTER_DELTA")
    assert plan.counts["NEW"] == 1
    assert plan.input_row_count == 2
    assert plan.identical_duplicate_count == 1
    assert plan.records[0].source_ordinals == (1, 2)
    assert len(plan.records[0].candidates) == 1


@pytest.mark.parametrize("second_value", ["101", None, "0"])
def test_conflicting_duplicates_reject_batch(observation, second_value):
    first = observation()
    second = observation(**{METRIC_COLUMNS[0]: second_value})
    plan = classify_records([first, first, second], [first], input_mode="FULL_SNAPSHOT")
    record = plan.records[0]
    assert plan.status == "REJECTED_CONFLICT"
    assert record.classification == Classification.CONFLICTING_DUPLICATE
    assert record.after is None
    assert len(record.candidates) == 2
    assert record.source_ordinals == (1, 2, 3)
    assert plan.identical_duplicate_count == 1


def test_revision_holds_new_records_in_the_same_batch(observation):
    plan = classify_records([observation(quarter=1, **{METRIC_COLUMNS[0]: "101"}), observation()],
                            [observation(quarter=1)], input_mode="FULL_SNAPSHOT")
    assert plan.counts["NEW"] == 1
    assert plan.counts["REVISED"] == 1
    assert plan.status == "WAITING_APPROVAL"


def test_same_inputs_produce_same_plan_without_mutation(observation):
    incoming = [observation(quarter=3)]
    accepted = [observation()]
    original_incoming, original_accepted = deepcopy(incoming), deepcopy(accepted)
    first = classify_records(incoming, accepted, input_mode="QUARTER_DELTA")
    second = classify_records(incoming, accepted, input_mode="QUARTER_DELTA")
    assert first == second
    assert incoming == original_incoming
    assert accepted == original_accepted
    with pytest.raises(AttributeError):
        first.records[0].after.year = 2000
    copy = first.records[0].after.as_dict()
    copy[METRIC_COLUMNS[0]] = None
    assert first.records[0].after.metrics[0] == Decimal("100")


def test_already_accepted_new_record_is_unchanged_on_rerun(observation):
    accepted = [canonicalize_record(observation())]
    incoming = [observation(quarter=3)]
    first = classify_records(incoming, accepted, input_mode="QUARTER_DELTA")
    # Simulate a separately accepted NEW observation only in this in-memory fixture.
    committed_fixture = [*accepted, first.records[0].after]
    rerun = classify_records(incoming, committed_fixture, input_mode="QUARTER_DELTA")
    assert first.counts["NEW"] == 1
    assert rerun.counts["UNCHANGED"] == 1
    assert rerun.counts["NEW"] == 0


def test_revision_reruns_keep_the_before_version_and_pending_state(observation):
    accepted = [observation()]
    incoming = [observation(**{METRIC_COLUMNS[0]: "99"})]
    first = classify_records(incoming, accepted, input_mode="FULL_SNAPSHOT")
    assert first == classify_records(incoming, accepted, input_mode="FULL_SNAPSHOT")
    assert first.status == "WAITING_APPROVAL"
    assert accepted[0][METRIC_COLUMNS[0]] == "100.00"
    assert first.records[0].before.payload_hash != first.records[0].after.payload_hash


def test_reordered_inputs_keep_key_classifications_and_hashes(observation):
    incoming = [observation(quarter=1), observation()]
    first = classify_records(incoming, input_mode="FULL_SNAPSHOT")
    reordered = classify_records(reversed(incoming), input_mode="FULL_SNAPSHOT")
    assert [(r.key, r.classification, r.after.payload_hash) for r in first.records] == [
        (r.key, r.classification, r.after.payload_hash) for r in reordered.records]


def test_full_snapshot_missing_accepted_period_is_rejected(observation):
    plan = classify_records([observation()], [observation(quarter=1), observation()], input_mode="FULL_SNAPSHOT")
    assert plan.status == "REJECTED_SCOPE"
    assert plan.missing_accepted_periods == ((2026, 1),)


def test_delta_allows_omitted_accepted_periods(observation):
    plan = classify_records([observation()], [observation(quarter=1), observation()], input_mode="QUARTER_DELTA")
    assert plan.status == "READY"
    assert plan.missing_accepted_periods == ()
    assert len(plan.records) == 1


def test_calendar_gap_and_delta_backfill(observation):
    gap = classify_records([observation(quarter=4)], [observation()], input_mode="QUARTER_DELTA")
    assert gap.status == "REJECTED_SCOPE"
    assert gap.calendar_gaps == ((2026, 3),)
    backfill = classify_records([observation()], [observation(quarter=1), observation(quarter=3)], input_mode="QUARTER_DELTA")
    assert backfill.status == "READY"
    assert backfill.calendar_gaps == ()


def test_declared_scope_reports_missing_and_unexpected_periods(observation):
    plan = classify_records([observation(quarter=3)], [observation()], input_mode="QUARTER_DELTA",
                            declared_periods=[(2026, 2)])
    assert plan.status == "REJECTED_SCOPE"
    assert plan.missing_declared_periods == ((2026, 2),)
    assert plan.unexpected_periods == ((2026, 3),)


def test_duplicate_declared_scope_is_rejected(observation):
    with pytest.raises(ValidationError, match="Declared scope"):
        classify_records([observation()], input_mode="QUARTER_DELTA", declared_periods=[(2026, 2), (2026, 2)])


@pytest.mark.parametrize("scope", [[2026], [(2026,)], [(2026, 2, 3)], ["2026-Q2"]])
def test_malformed_declared_scope_is_reported_as_validation_error(observation, scope):
    with pytest.raises(ValidationError, match="YEAR/QUARTER pairs"):
        classify_records([observation()], input_mode="QUARTER_DELTA", declared_periods=scope)


def test_replay_of_older_observation_requires_revision_review(observation):
    current = observation(**{METRIC_COLUMNS[0]: "101"})
    older = observation()
    plan = classify_records([older], [current], input_mode="FULL_SNAPSHOT")
    assert plan.status == "WAITING_APPROVAL"
    assert plan.records[0].before.metrics[0] == Decimal("101")
    assert plan.records[0].after.metrics[0] == Decimal("100")


@pytest.mark.parametrize("changed", [False, True])
def test_duplicate_accepted_keys_are_invalid(observation, changed):
    second = observation(**{METRIC_COLUMNS[0]: "101"}) if changed else observation()
    with pytest.raises(ValidationError, match="Accepted observations have duplicate"):
        classify_records([observation()], [observation(), second], input_mode="FULL_SNAPSHOT")


@pytest.mark.parametrize("value", ["-1", "NaN", "inf", "Infinity", "1,2", "1e3", Decimal("NaN"),
                                    Decimal("sNaN"), Decimal("Infinity"), Decimal("-1"), 1.0, True, {}])
def test_invalid_metrics_and_binary_floats_fail_validation(observation, value):
    with pytest.raises(ValidationError):
        classify_records([observation(**{METRIC_COLUMNS[0]: value})], input_mode="QUARTER_DELTA")


@pytest.mark.parametrize("column,value", [("YEAR", None), ("YEAR", "2026.5"), ("YEAR", "0"),
                                        ("YEAR", "10000"), ("QUARTER", ""), ("QUARTER", "5"),
                                        ("QUARTER", "0"), ("QUARTER", "2.5")])
def test_invalid_period_keys(observation, column, value):
    with pytest.raises(ValidationError):
        classify_records([observation(**{column: value})], input_mode="FULL_SNAPSHOT")


def test_schema_requires_all_metrics_and_recognized_names(observation):
    row = observation()
    row.pop(METRIC_COLUMNS[0])
    with pytest.raises(ValidationError, match="nine contract"):
        canonicalize_record(row)
    row = observation(EXTRA="ignored?")
    with pytest.raises(ValidationError, match="nine contract"):
        canonicalize_record(row)
    with pytest.raises(ValidationError):
        canonicalize_record("not a mapping")


def test_empty_input_and_unknown_mode(observation):
    with pytest.raises(ValidationError, match="no records"):
        classify_records([], input_mode="FULL_SNAPSHOT")
    with pytest.raises(ValueError):
        classify_records([observation()], input_mode="UNSPECIFIED")


def test_raw_and_staging_names_produce_same_canonical_payload(observation):
    staging = observation(YEAR="2,026.00", QUARTER="2.00")
    raw = {raw_name: staging[staging_name] for raw_name, staging_name in COLUMN_MAPPING}
    assert canonicalize_record(raw).payload_hash == canonicalize_record(staging).payload_hash


def test_large_decimal_precision_survives_small_decimal_context(observation):
    metric = METRIC_COLUMNS[0]
    old = observation(**{metric: "123456789012345678901234567890.000001"})
    new = observation(**{metric: "123456789012345678901234567890.000002"})
    with localcontext() as context:
        context.prec = 3
        plan = classify_records([new], [old], input_mode="FULL_SNAPSHOT")
        assert plan.status == "WAITING_APPROVAL"
        assert plan.records[0].changed_metrics == (metric,)
        assert plan.records[0].before.payload_hash != plan.records[0].after.payload_hash


def test_source_precision_change_is_detected_even_if_float_projection_matches(observation):
    first, second = "100.00000000000000001", "100.00000000000000002"
    assert float(first) == float(second)
    plan = classify_records([observation(**{METRIC_COLUMNS[0]: second})],
                            [observation(**{METRIC_COLUMNS[0]: first})], input_mode="FULL_SNAPSHOT")
    assert plan.status == "WAITING_APPROVAL"


def test_decimal_signed_zero_and_insignificant_zeros_hash_identically(observation):
    negative_zero = canonicalize_record(observation(**{METRIC_COLUMNS[0]: Decimal("-0.000")}))
    zero = canonicalize_record(observation(**{METRIC_COLUMNS[0]: "0"}))
    assert negative_zero.payload_hash == zero.payload_hash


def test_direct_canonical_records_validate_metric_count():
    with pytest.raises(ValidationError, match="seven metrics"):
        CanonicalRecord(2026, 2, (Decimal(1),))


def test_historical_baseline_and_equivalent_full_delta_updates(observation):
    source = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"
    loaded = load_tra_csv(source)
    baseline = WAREHOUSE_CONTRACT["historical_baseline"]
    rows = loaded.data.to_dict(orient="records")
    initial = classify_records(rows, input_mode="FULL_SNAPSHOT")
    accepted = tuple(record.after for record in initial.records)
    assert initial.status == "READY"
    assert initial.counts["NEW"] == 66
    assert accepted[0].key == (2010, 1)
    assert accepted[-1].key == (2026, 2)
    assert {name: sum(record.metrics[index] is None for record in accepted)
            for index, name in enumerate(METRIC_COLUMNS)} == baseline["metric_null_counts"]
    replay = classify_records(rows, accepted, input_mode="FULL_SNAPSHOT")
    assert replay.counts["UNCHANGED"] == 66
    assert replay.status == "READY"
    synthetic_update = observation(quarter=3)
    full = classify_records([*accepted, synthetic_update], accepted, input_mode="FULL_SNAPSHOT")
    delta = classify_records([synthetic_update], accepted, input_mode="QUARTER_DELTA", declared_periods=[(2026, 3)])
    assert full.counts["NEW"] == delta.counts["NEW"] == 1
    assert full.status == delta.status == "READY"
    assert full.records[-1].after == delta.records[0].after
    assert loaded.inspection["sha256"] == baseline["source_sha256"]


def test_classifier_uses_no_connection_or_sql(monkeypatch, observation):
    import socket
    from telecom_pipeline import snowflake_config

    def forbidden(*args, **kwargs):
        pytest.fail("The in-memory classifier attempted external access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)
    assert classify_records([observation()], input_mode="QUARTER_DELTA").status == "READY"
