import hashlib
from pathlib import Path

import pandas as pd
import pytest

from telecom_pipeline.ingestion import SourceFormatError, load_tra_csv


SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"
SOURCE_HASH = "12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd"


def test_actual_source():
    before = SOURCE.read_bytes()
    result = load_tra_csv(SOURCE)
    assert result.data.shape == (66, 9)
    assert result.inspection["metadata_lines"] == 6
    assert result.inspection["english_header_line"] == 7
    assert result.inspection["empty_rows_skipped"] == 1
    assert result.inspection["arabic_columns"][:2] == ["\u0627\u0644\u0633\u0646\u0629", "\u0627\u0644\u0631\u0628\u0639"]
    assert all("\ufffd" not in name for name in result.inspection["arabic_columns"])
    assert result.inspection["duplicate_records"] == 0
    assert result.inspection["declared_records"] == 68
    assert result.inspection["record_count_matches_metadata"] is False
    assert result.data.iloc[0]["Year"] == "2,026.00"
    assert result.data.iloc[-1]["Quarter"] == "1.00"
    assert pd.isna(result.data.iloc[-1]["International Internet Capacity Mbps"])
    assert SOURCE.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == SOURCE_HASH


def export(tmp_path, body, count=1):
    path = tmp_path / "sample.csv"
    path.write_text(f"# Records: {count}\nYear,Quarter,Metric\nالسنة,الربع,قيمة\n,,,\n" + body, encoding="utf-8-sig")
    return path


def test_bom_quoted_values_null_and_repeatability(tmp_path):
    path = export(tmp_path, '"2,026.00",2.00,\n')
    first = load_tra_csv(path)
    pd.testing.assert_frame_equal(first.data, load_tra_csv(path).data)
    assert first.data.iloc[0, 0] == "2,026.00"
    assert pd.isna(first.data.iloc[0, 2])


@pytest.mark.parametrize("body,count", [("2026,1\n", 1), ("", 0)])
def test_invalid_structure_or_count(tmp_path, body, count):
    with pytest.raises(SourceFormatError):
        load_tra_csv(export(tmp_path, body, count))


def test_duplicates_preserved_and_reported(tmp_path):
    result = load_tra_csv(export(tmp_path, "2026,1,5\n2026,1,5\n", 2))
    assert len(result.data) == 2
    assert result.inspection["duplicate_records"] == 1


def test_invalid_encoding(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"\xffYear,Quarter")
    with pytest.raises(SourceFormatError):
        load_tra_csv(path)


def test_na_literal_preserved(tmp_path):
    assert load_tra_csv(export(tmp_path, "2026,1,NA\n")).data.iloc[0, 2] == "NA"
