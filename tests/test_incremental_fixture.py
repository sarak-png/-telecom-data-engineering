"""Synthetic source-format fixture versus an invented in-memory warehouse baseline.

All metric values below and in fixtures/synthetic_tra_incremental.csv are
invented engineering examples. They are not real TRA measurements.
"""
from decimal import Decimal
from pathlib import Path
import socket

import pytest

from telecom_pipeline.incremental import Classification, classify_records
from telecom_pipeline.ingestion import load_tra_csv
from telecom_pipeline.readers import read_source
from telecom_pipeline import snowflake_config


FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_tra_incremental.csv"


@pytest.fixture
def synthetic_warehouse_baseline():
    """Two complete synthetic quarters, expressed in contract STAGING aliases."""
    return [
        {"YEAR": 2026, "QUARTER": 1, "OUTGOING_INT_MIN_M": "101",
         "INCOMING_INT_MIN_M": "201", "MOBILE_LOCAL_MIN_M": "301",
         "FIXED_LOCAL_MIN_M": None, "FIXED_BROADBAND_GB_M": "501",
         "MOBILE_BROADBAND_GB_M": "601", "INTERNET_CAPACITY_MBPS": "701"},
        {"YEAR": 2026, "QUARTER": 2, "OUTGOING_INT_MIN_M": "102",
         "INCOMING_INT_MIN_M": "202", "MOBILE_LOCAL_MIN_M": "302",
         "FIXED_LOCAL_MIN_M": "402", "FIXED_BROADBAND_GB_M": None,
         "MOBILE_BROADBAND_GB_M": "602", "INTERNET_CAPACITY_MBPS": "702"},
    ]


def test_synthetic_fixture_matches_bilingual_source_layout():
    result = load_tra_csv(FIXTURE)
    inspection = result.inspection
    contract_columns = [column["name"] for column in
                        snowflake_config.WAREHOUSE_CONTRACT["objects"]["raw_source"]["columns"]]
    assert list(result.data.columns) == contract_columns
    assert result.data.shape == (3, 9)
    assert inspection["metadata_lines"] == 6
    assert inspection["english_header_line"] == 7
    assert inspection["empty_rows_skipped"] == 1
    assert len(inspection["arabic_columns"]) == 9
    assert inspection["arabic_columns"][0] == "السنة"
    assert inspection["record_count_matches_metadata"] is True
    assert inspection["duplicate_records"] == 0
    assert result.data.iloc[0]["Year"] == "2,026.00"
    assert "SYNTHETIC" in FIXTURE.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["FULL_SNAPSHOT", "QUARTER_DELTA"])
def test_synthetic_fixture_classifications_and_safe_rerun(mode, synthetic_warehouse_baseline, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic fixture test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)
    before = FIXTURE.read_bytes()
    incoming = read_source(FIXTURE).data.to_dict(orient="records")
    plan = classify_records(incoming, synthetic_warehouse_baseline, input_mode=mode)
    assert plan.counts == {"NEW": 1, "UNCHANGED": 1, "REVISED": 1, "CONFLICTING_DUPLICATE": 0}
    by_key = {row.key: row for row in plan.records}
    assert by_key[(2026, 1)].classification == Classification.UNCHANGED
    assert by_key[(2026, 2)].classification == Classification.REVISED
    assert by_key[(2026, 3)].classification == Classification.NEW
    revision = by_key[(2026, 2)]
    assert revision.changed_metrics == ("OUTGOING_INT_MIN_M",)
    assert revision.before.metrics[0] == Decimal("102")
    assert revision.after.metrics[0] == Decimal("112")
    assert revision.before.metrics[4] is revision.after.metrics[4] is None
    assert by_key[(2026, 1)].after.metrics[3] is None
    assert by_key[(2026, 3)].before is None
    assert plan.status == "WAITING_APPROVAL"
    assert plan.requires_approval
    assert plan.calendar_gaps == ()
    assert plan.input_row_count == 3
    assert plan.identical_duplicate_count == 0
    assert classify_records(incoming, synthetic_warehouse_baseline, input_mode=mode) == plan
    assert synthetic_warehouse_baseline[1]["OUTGOING_INT_MIN_M"] == "102"
    assert FIXTURE.read_bytes() == before
