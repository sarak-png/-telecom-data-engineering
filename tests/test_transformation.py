from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from telecom_pipeline.readers import read_source
from telecom_pipeline.transformation import build, save, serialize, transform, warehouse_decimal
from telecom_pipeline.validation import COLUMNS, ValidationError


SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"


def test_actual_typed_tables_and_lineage():
    result = read_source(SOURCE)
    staging, mart, quality = transform(result.data, result.inspection["sha256"])
    assert staging.shape == (66, 11)
    assert mart.shape == (66, 11)
    assert mart.iloc[0]["QUARTER_START_DATE"] == date(2026, 4, 1)
    assert mart.iloc[-1]["QUARTER_START_DATE"] == date(2010, 1, 1)
    assert staging.iloc[0]["FIXED_LOCAL_MINUTES_MILLION"] == Decimal("27.8627")
    assert str(staging["YEAR"].dtype) == "Int64"
    assert sum(quality["missing_values"].values()) == 84
    assert sum(staging.isna().sum()) == 84
    assert staging.iloc[-1]["MOBILE_LOCAL_MINUTES_MILLION"] is None
    assert staging["RECORD_HASH"].is_unique
    from telecom_pipeline.snowflake_loader import prepare
    _, raw = prepare(SOURCE)
    assert staging.iloc[0]["RECORD_HASH"] == raw[0]["record_hash"]


@pytest.mark.parametrize("quarter,month", [(1, 1), (2, 4), (3, 7), (4, 10)])
def test_quarter_dates(quarter, month):
    frame = read_source(SOURCE).data.iloc[:1].copy()
    frame.loc[frame.index[0], "Quarter"] = str(quarter)
    _, mart, _ = transform(frame, "test-source")
    assert mart.iloc[0]["QUARTER_START_DATE"] == date(2026, month, 1)


def test_duplicate_policy_and_conflict():
    frame = read_source(SOURCE).data.iloc[:1]
    duplicate = pd.concat([frame, frame], ignore_index=True)
    staging, _, quality = transform(duplicate, "test-source")
    assert len(staging) == 1
    assert quality["duplicate_rows"] == 1
    duplicate.loc[1, COLUMNS[2]] = "99"
    with pytest.raises(ValidationError):
        transform(duplicate, "test-source")


@pytest.mark.parametrize("value", ["0.0000001", "1" * 33])
def test_reject_snowflake_precision_loss(value):
    with pytest.raises(ValidationError):
        warehouse_decimal(value)


def test_export_repeatability_no_overwrite_and_source_protection(tmp_path):
    original = SOURCE.read_bytes()
    report = build(SOURCE)
    output = tmp_path / "prepared.json"
    assert save(report, output, source=SOURCE) == "CREATED"
    assert save(build(SOURCE), output, source=SOURCE) == "REUSED"
    data = json.loads(output.read_text())
    assert data["mart"][0]["QUARTER_START_DATE"] == "2026-04-01"
    assert data["staging"][0]["FIXED_LOCAL_MINUTES_MILLION"] == "27.8627"
    assert json.loads(serialize(report))["staging"][-1]["MOBILE_LOCAL_MINUTES_MILLION"] is None
    report["transform_version"] = "different-version"
    with pytest.raises(FileExistsError):
        save(report, output, source=SOURCE)
    with pytest.raises(ValueError):
        save(report, SOURCE, source=SOURCE)
    assert SOURCE.read_bytes() == original
