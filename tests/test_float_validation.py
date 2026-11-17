"""Mocked SELECT-only FLOAT conversion validation with original and synthetic inputs."""
import csv
from decimal import Decimal
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from telecom_pipeline import float_validation, snowflake_config
from telecom_pipeline.float_validation import validate_conversion
from telecom_pipeline.incremental import COLUMN_MAPPING, canonicalize_record
from telecom_pipeline.readers import read_source
from telecom_pipeline.snowflake_readonly import READ_COLUMNS, SELECT_QUARTERLY, ReadOnlyError


SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"
ROLE = {"SNOWFLAKE_ROLE": "TELECOM_READONLY"}


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Test attempted a live connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(snowflake_config, "connect", forbidden)


def mock_connection(rows):
    state = SimpleNamespace(statements=[], closed=False)
    def execute(statement):
        state.statements.append(statement)
        assert statement == SELECT_QUARTERLY
    cursor = SimpleNamespace(execute=execute, description=[(name,) for name in READ_COLUMNS],
                             fetchall=lambda: rows, close=lambda: None)
    def close():
        state.closed = True
    state.cursor = lambda: cursor
    state.close = close
    return state


def synthetic_source(tmp_path, value):
    path = tmp_path / "synthetic-float-values.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([raw for raw, _ in COLUMN_MAPPING])
        writer.writerow([2026, 1, value, "1", "1", "1", "1", "1", "1"])
    return path


def test_actual_csv_mocked_float_projection_preserves_original(tmp_path):
    before = SOURCE.read_bytes()
    records = [canonicalize_record(row) for row in read_source(SOURCE).data.to_dict(orient="records")]
    native = [(row.year, row.quarter, *(float(value) if value is not None else None for value in row.metrics))
              for row in records]
    connection = mock_connection(native)
    report = validate_conversion(SOURCE, connector=lambda: connection, environ=ROLE)
    assert report["source_is_historical_snapshot"]
    assert report["source_periods"] == report["warehouse_periods"] == 66
    assert report["metric_checks"] == 462
    assert report["counts"]["NULL_MATCH"] == 84
    assert report["counts"]["EXPECTED_BINARY_REPRESENTATION"] > 0
    assert report["differences_requiring_review"] == []
    assert report["status"] == "PROJECTION_CHECKS_PASSED"
    assert report["publication_allowed"] is False
    assert connection.statements == [SELECT_QUARTERLY]
    assert connection.closed
    assert SOURCE.read_bytes() == before


@pytest.mark.parametrize("source,observed,expected", [
    ("0.1", 0.1, "EXPECTED_BINARY_REPRESENTATION"),
    ("1.25", 1.25, "EXACT_NUMERIC_MATCH"),
    ("0", 0.0, "EXACT_NUMERIC_MATCH"),
    (None, None, "NULL_MATCH"),
    (None, 0.0, "NULL_MISMATCH"),
    ("0", None, "NULL_MISMATCH"),
    ("1", math.nextafter(1.0, math.inf), "UNEXPLAINED_FLOAT_MISMATCH"),
    ("0.100000000000000005", 0.1, "EXPECTED_BINARY_REPRESENTATION"),
    ("0." + "0" * 399 + "1", 0.0, "UNDERFLOW"),
])
def test_comparison_effects_without_tolerances(tmp_path, source, observed, expected):
    path = synthetic_source(tmp_path, source)
    connection = mock_connection([(2026, 1, observed, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])
    report = validate_conversion(path, connector=lambda: connection, environ=ROLE)
    check = report["metrics"][READ_COLUMNS[2]]["samples"][0]
    assert check["result"] == expected
    assert check["source_decimal_text"] == source
    assert check["source_is_zero"] == (source == "0")
    assert report["source_is_historical_snapshot"] is False
    assert report["publication_allowed"] is False
    if expected == "EXPECTED_BINARY_REPRESENTATION":
        assert Decimal(check["observed_exact_binary_decimal"]) != Decimal(source)
        assert check["ulp_distance_from_projection"] == "0"
    if expected == "UNEXPLAINED_FLOAT_MISMATCH":
        assert check["ulp_distance_from_projection"] == "1"
        assert report["status"] == "REQUIRES_REVIEW"


def test_missing_quarter_and_extra_warehouse_period_are_explicit(tmp_path):
    path = synthetic_source(tmp_path, "1")
    connection = mock_connection([(2026, 2, *([1.0] * 7))])
    report = validate_conversion(path, connector=lambda: connection, environ=ROLE)
    assert report["counts"] == {"MISSING_PERIOD": 7}
    assert len(report["differences_requiring_review"]) == 7
    assert report["extra_warehouse_periods"] == [[2026, 2]]


@pytest.mark.parametrize("role", ["ACCOUNTADMIN", "SYSADMIN", "", "OTHER_ROLE"])
def test_readonly_role_required_before_connecting(role):
    def forbidden():
        pytest.fail("Wrong role attempted a connection")
    with pytest.raises(ReadOnlyError, match="TELECOM_READONLY_ROLE_REQUIRED"):
        validate_conversion(SOURCE, connector=forbidden, environ={"SNOWFLAKE_ROLE": role})


def test_explicit_live_opt_in_is_required(monkeypatch, capsys):
    assert float_validation.main([str(SOURCE)]) == 1
    report = json.loads(capsys.readouterr().err)
    assert report["error_code"] == "LIVE_READ_OPT_IN_REQUIRED"
    assert report["publication_allowed"] is False


def test_cli_with_mocked_reader_reports_review_exit_code(tmp_path, monkeypatch, capsys):
    path = synthetic_source(tmp_path, "1")
    monkeypatch.setenv("SNOWFLAKE_ROLE", "TELECOM_READONLY")
    original_fetch = float_validation.fetch_quarterly_records
    monkeypatch.setattr(float_validation, "fetch_quarterly_records",
        lambda **options: original_fetch(connector=lambda: mock_connection([])))
    assert float_validation.main([str(path), "--live-read-only"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REQUIRES_REVIEW"


def test_cli_connection_errors_are_sanitized(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("secret-token private-user private-account")
    monkeypatch.setenv("SNOWFLAKE_ROLE", "TELECOM_READONLY")
    original_fetch = float_validation.fetch_quarterly_records
    monkeypatch.setattr(float_validation, "fetch_quarterly_records",
        lambda **options: original_fetch(connector=fail))
    assert float_validation.main([str(SOURCE), "--live-read-only", "--diagnostics"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert all(secret not in output.err for secret in ("secret-token", "private-user", "private-account"))
    result = json.loads(output.err)
    assert result["diagnostics"]["stage"] == "CONNECT"
    assert result["publication_allowed"] is False


def test_malformed_input_fails_before_reading_warehouse(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("bad,headers\n1,2\n", encoding="utf-8")
    def forbidden():
        pytest.fail("Malformed source attempted a connection")
    with pytest.raises(ValueError):
        validate_conversion(path, connector=forbidden, environ=ROLE)


def test_same_snapshot_has_deterministic_report(tmp_path):
    path = synthetic_source(tmp_path, "0.1")
    snapshot = float_validation.fetch_quarterly_records(
        connector=lambda: mock_connection([(2026, 1, 0.1, *([1.0] * 6))]))
    source = read_source(path)
    assert float_validation.compare_snapshot(source, snapshot) == float_validation.compare_snapshot(source, snapshot)


@pytest.mark.parametrize("arguments,expected_exit", [([], 1), (["--help"], 0)])
def test_module_entry_point_invokes_main_offline(arguments, expected_exit):
    result = subprocess.run(
        [sys.executable, "-m", "telecom_pipeline.float_validation", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == expected_exit
    if arguments:
        assert "usage:" in result.stdout
        assert "--live-read-only" in result.stdout
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert json.loads(result.stderr) == {
            "status": "ERROR",
            "error_code": "LIVE_READ_OPT_IN_REQUIRED",
            "publication_allowed": False,
        }
