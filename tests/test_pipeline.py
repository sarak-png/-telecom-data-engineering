"""Synthetic fixtures exercise engineering behavior; no fabricated TRA facts."""
import json
import logging
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from telecom_pipeline import pipeline
from telecom_pipeline.ingestion import SourceFormatError
from telecom_pipeline.readers import read_source
from telecom_pipeline.validation import COLUMNS, ValidationError, validate


SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"


def sample():
    return pd.DataFrame([["2,026.00", "2.00", "1.50", "2", "3", "4", "", "6", "7"]],
                        columns=COLUMNS, dtype="string")


def fixture_file(tmp_path, frame=None, name="synthetic.csv"):
    path = tmp_path / "sources" / name
    path.parent.mkdir(exist_ok=True)
    frame = sample() if frame is None else frame
    if path.suffix == ".xlsx":
        frame.to_excel(path, index=False)
    else:
        frame.to_csv(path, index=False)
    return path


def count_records(state):
    with sqlite3.connect(state / "ingestion.sqlite3") as conn:
        return conn.execute("SELECT count(*) FROM records").fetchone()[0]


def test_real_source_rerun_and_logs(tmp_path, caplog):
    original = SOURCE.read_bytes()
    caplog.set_level(logging.INFO, logger="telecom_pipeline")
    state = tmp_path / "ledger"
    first = pipeline.run(SOURCE, state_dir=state)
    second = pipeline.run(SOURCE, state_dir=state)
    assert first["quality"]["rows_read"] == 66
    assert first["inserted_rows"] == 66
    assert second["inserted_rows"] == 0
    assert second["existing_rows"] == 66
    assert first["batch_id"] != second["batch_id"]
    assert first["warnings"]
    assert sum(first["quality"]["missing_values"].values()) == 84
    assert count_records(state) == 66
    assert SOURCE.read_bytes() == original
    assert [json.loads(record.message)["event"] for record in caplog.records] == [
        "batch_started", "batch_succeeded", "batch_started", "batch_succeeded"]
    with sqlite3.connect(state / "ingestion.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM batches WHERE status='SUCCEEDED'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM batch_records").fetchone()[0] == 132


def test_csv_excel_equivalence(tmp_path):
    csv = fixture_file(tmp_path)
    xlsx = fixture_file(tmp_path, name="synthetic.xlsx")
    state = tmp_path / "state"
    assert pipeline.run(csv, state_dir=state)["inserted_rows"] == 1
    assert pipeline.run(xlsx, state_dir=state)["inserted_rows"] == 0
    assert validate(read_source(csv).data) == validate(read_source(xlsx).data)


@pytest.mark.parametrize("column,value", [
    ("Year", None), ("Quarter", "5"), ("Year", "2026.5"),
    (COLUMNS[2], "NaN"), (COLUMNS[2], "-1"), (COLUMNS[2], "1,2"),
    (COLUMNS[2], "Infinity"), ("Year", "1800"),
])
def test_invalid_values(column, value):
    frame = sample()
    frame.loc[0, column] = value
    with pytest.raises(ValidationError):
        validate(frame)


def test_schema_and_empty():
    with pytest.raises(ValidationError):
        validate(sample().drop(columns=[COLUMNS[-1]]))
    with pytest.raises(ValidationError):
        validate(sample().iloc[:0])


def test_duplicate_and_conflicting_periods():
    frame = pd.concat([sample(), sample()], ignore_index=True)
    records, quality = validate(frame)
    assert len(records) == 1
    assert quality["duplicate_rows"] == 1
    frame.loc[1, COLUMNS[2]] = "123"
    with pytest.raises(ValidationError, match="Conflicting"):
        validate(frame)


def test_changed_observations_and_new_period(tmp_path):
    source = fixture_file(tmp_path)
    state = tmp_path / "state"
    pipeline.run(source, state_dir=state)
    changed = sample()
    changed.loc[0, COLUMNS[2]] = "123"
    source = fixture_file(tmp_path, changed)
    assert pipeline.run(source, state_dir=state)["inserted_rows"] == 1
    assert count_records(state) == 2
    assert pipeline.run(source, state_dir=state)["inserted_rows"] == 0
    changed.loc[0, "Quarter"] = "3"
    fixture_file(tmp_path, changed)
    assert pipeline.run(source, state_dir=state)["inserted_rows"] == 1


def test_atomic_rollback_and_failure_history(tmp_path, monkeypatch):
    source = fixture_file(tmp_path)
    state = tmp_path / "state"
    original = pipeline.persist

    def broken(*args):
        original(*args)
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(pipeline, "persist", broken)
    with pytest.raises(RuntimeError):
        pipeline.run(source, state_dir=state)
    assert count_records(state) == 0
    with sqlite3.connect(state / "ingestion.sqlite3") as conn:
        assert conn.execute("SELECT status FROM batches").fetchone()[0] == "FAILED"
        assert conn.execute("SELECT count(*) FROM batch_records").fetchone()[0] == 0
    monkeypatch.setattr(pipeline, "persist", original)
    assert pipeline.run(source, state_dir=state)["inserted_rows"] == 1


def test_validation_failure_does_not_load(tmp_path):
    frame = sample()
    frame.loc[0, "Quarter"] = "0"
    source = fixture_file(tmp_path, frame)
    state = tmp_path / "state"
    with pytest.raises(ValidationError):
        pipeline.run(source, state_dir=state)
    assert count_records(state) == 0


def test_missing_file_is_audited(tmp_path):
    state = tmp_path / "ledger"
    with pytest.raises(FileNotFoundError):
        pipeline.run(tmp_path / "sources" / "absent.csv", state_dir=state)
    with sqlite3.connect(state / "ingestion.sqlite3") as conn:
        assert conn.execute("SELECT status FROM batches").fetchone()[0] == "FAILED"


def test_readers_reject_bad_structure_and_formulas(tmp_path):
    malformed = tmp_path / "bad.csv"
    malformed.write_text("Year,Quarter\n2026,1,extra\n")
    with pytest.raises(SourceFormatError):
        read_source(malformed)
    malformed.write_text("Year,Year\n2026,1\n")
    with pytest.raises(SourceFormatError):
        read_source(malformed)
    path = fixture_file(tmp_path, name="synthetic.xlsx")
    from openpyxl import load_workbook
    workbook = load_workbook(path)
    workbook.active["A2"] = "=2026"
    workbook.save(path)
    workbook.close()
    with pytest.raises(SourceFormatError, match="formulas"):
        read_source(path)


def test_explicit_encoding(tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_bytes('Year,Quarter\n2026,é\n'.encode("cp1252"))
    with pytest.raises(SourceFormatError):
        read_source(path)
    assert read_source(path, encoding="cp1252").data.iloc[0, 1] == "é"


def test_state_directory_cannot_be_in_source_directory(tmp_path):
    source = fixture_file(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        pipeline.run(source, state_dir=source.parent / "state")


def test_numeric_formatting_does_not_duplicate_records(tmp_path):
    source = fixture_file(tmp_path)
    state = tmp_path / "ledger"
    pipeline.run(source, state_dir=state)
    frame = sample()
    frame.loc[0, "Year"] = "2026"
    frame.loc[0, "Quarter"] = "2"
    frame.loc[0, COLUMNS[2]] = "1.5000"
    fixture_file(tmp_path, frame)
    assert pipeline.run(source, state_dir=state)["inserted_rows"] == 0


def test_named_excel_sheet(tmp_path):
    path = fixture_file(tmp_path, name="synthetic.xlsx")
    assert len(read_source(path, sheet="Sheet1").data) == 1


def test_concurrent_reruns(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    source = fixture_file(tmp_path)
    state = tmp_path / "ledger"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(pipeline.run, source, state_dir=state) for _ in range(2)]
        reports = [future.result() for future in futures]
    assert sum(report["inserted_rows"] for report in reports) == 1
    assert count_records(state) == 1
