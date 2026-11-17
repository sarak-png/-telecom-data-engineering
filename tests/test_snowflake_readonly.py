"""Mocked read-only integration. No test opens a Snowflake connection."""
import csv
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

import pytest

from telecom_pipeline.batch_control import BatchRegistry
from telecom_pipeline.incremental import METRIC_COLUMNS, COLUMN_MAPPING
from telecom_pipeline import readonly_dry_run, snowflake_readonly
from telecom_pipeline.snowflake_readonly import (
    COMPARISON_VERSION, READ_COLUMNS, SELECT_QUARTERLY, TABLE, ReadOnlyError,
    fetch_quarterly_records, float_comparison_value,
)


class MockCursor:
    def __init__(self, rows=(), columns=READ_COLUMNS, failure=None, close_failure=False):
        self.rows = rows
        self.description = [(name,) for name in columns]
        self.failure = failure
        self.close_failure = close_failure
        self.statements = []
        self.closed = False

    def execute(self, statement):
        self.statements.append(statement)
        assert statement == SELECT_QUARTERLY  # no general query or warehouse writes
        if self.failure == "execute":
            raise RuntimeError("password=DO_NOT_LEAK account=private")

    def fetchall(self):
        if self.failure == "fetch":
            raise RuntimeError("token=DO_NOT_LEAK")
        return self.rows

    def close(self):
        self.closed = True
        if self.close_failure:
            raise RuntimeError("secret close error")


class MockConnection:
    def __init__(self, rows=(), *, cursor_failure=False, **kwargs):
        self.reader = MockCursor(rows, **kwargs)
        self.cursor_failure = cursor_failure
        self.closed = False

    def cursor(self):
        if self.cursor_failure:
            raise RuntimeError("credential=DO_NOT_LEAK")
        return self.reader

    def close(self):
        self.closed = True


def warehouse_row(quarter=2, **changes):
    row = {"YEAR": 2026, "QUARTER": quarter, **dict.fromkeys(METRIC_COLUMNS, 100.0), **changes}
    return tuple(row[name] for name in READ_COLUMNS)


def source_row(quarter=2, **changes):
    return {"YEAR": "2026", "QUARTER": str(quarter), **dict.fromkeys(METRIC_COLUMNS, "100"), **changes}


def csv_file(tmp_path, rows, names=READ_COLUMNS):
    path = tmp_path / "synthetic-telecom.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    return path


def environment():
    return {f"SNOWFLAKE_{key}": value for key, value in {
        "ACCOUNT": "synthetic-account", "USER": "synthetic-user", "DATABASE": "TELECOM_DB",
        "WAREHOUSE": "EXISTING_WH", "ROLE": "READ_ROLE", "AUTHENTICATOR": "externalbrowser",
    }.items()}


def test_only_expected_nine_columns_and_one_select():
    conn = MockConnection([warehouse_row()])
    snapshot = fetch_quarterly_records(connector=lambda: conn)
    assert TABLE == "TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY"
    assert conn.reader.statements == [SELECT_QUARTERLY]
    assert SELECT_QUARTERLY.startswith('SELECT "YEAR", "QUARTER", ')
    assert "*" not in SELECT_QUARTERLY
    assert len(snapshot.records[0].metrics) == 7
    assert snapshot.records[0].metrics == (Decimal("100.0"),) * 7
    assert conn.closed and conn.reader.closed


@pytest.mark.parametrize("value", [0.0, -0.0, 0.1, 5e-324, 1.7976931348623157e308])
def test_float_representation_preserves_round_trip_not_original_precision(value):
    result = float_comparison_value(value)
    assert result == Decimal(str(value))
    assert float(result) == value


def test_float_decimal_conversion_is_explicitly_not_exact_binary_expansion():
    assert float_comparison_value(0.1) == Decimal("0.1")
    assert float_comparison_value(0.1) != Decimal.from_float(0.1)
    assert float_comparison_value(None) is None
    assert float(Decimal("0.100000000000000005")) == 0.1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -0.1, "0.1", 1, True, Decimal("0.1")])
def test_bad_warehouse_metrics_fail_closed(value):
    conn = MockConnection([warehouse_row(**{METRIC_COLUMNS[0]: value})])
    with pytest.raises(ReadOnlyError, match="INVALID_WAREHOUSE_METRIC"):
        fetch_quarterly_records(connector=lambda: conn)
    assert conn.closed and conn.reader.closed


@pytest.mark.parametrize("changes", [{"YEAR": None}, {"YEAR": 2026.0}, {"YEAR": True},
                                     {"QUARTER": 5}, {"QUARTER": Decimal("1.5")}])
def test_bad_warehouse_periods_fail_closed(changes):
    conn = MockConnection([warehouse_row(**changes)])
    with pytest.raises(ReadOnlyError, match="INVALID_WAREHOUSE_PERIOD"):
        fetch_quarterly_records(connector=lambda: conn)
    assert conn.closed


def test_decimal_integer_keys_supported():
    conn = MockConnection([warehouse_row(YEAR=Decimal("2026"), QUARTER=Decimal("2"))])
    assert fetch_quarterly_records(connector=lambda: conn).records[0].key == (2026, 2)


def test_duplicate_warehouse_periods_rejected():
    with pytest.raises(ReadOnlyError, match="DUPLICATE_WAREHOUSE_PERIOD"):
        fetch_quarterly_records(connector=lambda: MockConnection([warehouse_row(), warehouse_row()]))


@pytest.mark.parametrize("columns,rows", [(tuple(reversed(READ_COLUMNS)), []),
                                           (READ_COLUMNS, [(2026, 2)])])
def test_schema_or_row_width_drift_rejected(columns, rows):
    conn = MockConnection(rows, columns=columns)
    with pytest.raises(ReadOnlyError, match="WAREHOUSE_COLUMN_MISMATCH"):
        fetch_quarterly_records(connector=lambda: conn)
    assert conn.closed and conn.reader.closed


@pytest.mark.parametrize("failure", ["execute", "fetch", "cursor"])
def test_read_failure_is_redacted_and_resources_closed(failure):
    conn = MockConnection(cursor_failure=failure == "cursor", failure=failure, close_failure=True)
    with pytest.raises(ReadOnlyError) as error:
        fetch_quarterly_records(connector=lambda: conn)
    assert str(error.value) == "WAREHOUSE_READ_FAILED"
    assert error.value.__suppress_context__
    assert conn.closed
    assert conn.reader.closed == (failure != "cursor")


def test_connection_failure_redacted():
    def broken():
        raise RuntimeError("password=DO_NOT_LEAK")
    with pytest.raises(ReadOnlyError, match="^WAREHOUSE_READ_FAILED$"):
        fetch_quarterly_records(connector=broken)


def test_live_opt_in_required_before_environment_or_connector_import(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Configuration accessed without live opt-in")
    monkeypatch.setattr(snowflake_readonly, "connection_options", forbidden)
    with pytest.raises(ReadOnlyError, match="LIVE_READ_OPT_IN_REQUIRED"):
        fetch_quarterly_records()


def test_live_branch_reuses_safe_configuration_with_mocked_sdk(monkeypatch):
    import snowflake.connector
    conn = MockConnection([warehouse_row()])
    seen = {}
    def connect(**options):
        seen.update(options)
        return conn
    monkeypatch.setattr(snowflake.connector, "connect", connect)
    fetch_quarterly_records(allow_live=True, environ=environment())
    assert seen["database"] == "TELECOM_DB"
    assert seen["warehouse"] == "EXISTING_WH"
    assert seen["authenticator"] == "externalbrowser"
    assert seen["client_store_temporary_credential"] is False
    assert seen["session_parameters"]["QUERY_TAG"] == "telecom_de_readonly_dry_run"
    assert conn.reader.statements == [SELECT_QUARTERLY]


@pytest.mark.parametrize("database", ["OTHER_DB", "CUSTOMERS", "TELECOM_DB;DROP"])
def test_configured_database_must_match_contract_before_connection(monkeypatch, database):
    import snowflake.connector
    def forbidden(**options):
        pytest.fail("Unexpected connection")
    monkeypatch.setattr(snowflake.connector, "connect", forbidden)
    env = environment()
    env["SNOWFLAKE_DATABASE"] = database
    with pytest.raises(ReadOnlyError):
        fetch_quarterly_records(allow_live=True, environ=env)


def test_empty_warehouse_is_valid_snapshot():
    snapshot = fetch_quarterly_records(connector=lambda: MockConnection())
    assert snapshot.records == ()
    assert len(snapshot.sha256) == 64


def test_mixed_new_unchanged_revised_and_null_transitions(tmp_path):
    path = csv_file(tmp_path, [source_row(1), source_row(2, **{METRIC_COLUMNS[0]: None}), source_row(3)])
    conn = MockConnection([warehouse_row(1), warehouse_row(2)])
    report = readonly_dry_run.dry_run(path, input_mode="FULL_SNAPSHOT", connector=lambda: conn)
    assert report["counts"] == {"NEW": 1, "UNCHANGED": 1, "REVISED": 1, "CONFLICTING_DUPLICATE": 0}
    assert report["classification_status"] == "WAITING_APPROVAL"
    assert report["requires_approval"]
    assert report["comparison_representation"] == COMPARISON_VERSION
    assert report["warehouse_writes"] is False
    assert report["batch_id"] is None
    assert report["revisions"][0]["changed_metrics"] == (METRIC_COLUMNS[0],)


def test_null_to_value_is_revision(tmp_path):
    path = csv_file(tmp_path, [source_row(**{METRIC_COLUMNS[0]: "0"})])
    conn = MockConnection([warehouse_row(**{METRIC_COLUMNS[0]: None})])
    report = readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=lambda: conn)
    assert report["counts"]["REVISED"] == 1


def test_source_precision_difference_is_not_silently_tolerated(tmp_path):
    path = csv_file(tmp_path, [source_row(**{METRIC_COLUMNS[0]: "0.100000000000000005"})])
    conn = MockConnection([warehouse_row(**{METRIC_COLUMNS[0]: 0.1})])
    report = readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=lambda: conn)
    assert report["counts"]["REVISED"] == 1
    assert "not proof" in report["comparison_note"]


def test_float_representation_collision_is_disclosed_not_precision_recovery(tmp_path):
    path = csv_file(tmp_path, [source_row(**{METRIC_COLUMNS[0]: "0.1"})])
    # An original 0.100000000000000005 would have produced the same stored float.
    conn = MockConnection([warehouse_row(**{METRIC_COLUMNS[0]: float(Decimal("0.100000000000000005"))})])
    report = readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=lambda: conn)
    assert report["counts"]["UNCHANGED"] == 1
    assert "approximate" in report["comparison_note"]


def test_delta_omissions_allowed_full_snapshot_omissions_rejected(tmp_path):
    path = csv_file(tmp_path, [source_row(3)])
    def connector():
        return MockConnection([warehouse_row(1), warehouse_row(2)])
    delta = readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=connector)
    full = readonly_dry_run.dry_run(path, input_mode="FULL_SNAPSHOT", connector=connector)
    assert delta["classification_status"] == "READY"
    assert delta["counts"]["NEW"] == 1
    assert full["classification_status"] == "REJECTED_SCOPE"
    assert full["missing_accepted_periods"] == ((2026, 1), (2026, 2))


def test_conflicting_input_reported_without_publication(tmp_path):
    path = csv_file(tmp_path, [source_row(), source_row(**{METRIC_COLUMNS[0]: "99"})])
    report = readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=lambda: MockConnection())
    assert report["counts"]["CONFLICTING_DUPLICATE"] == 1
    assert report["classification_status"] == "REJECTED_CONFLICT"


def test_optional_registry_idempotent_and_never_approves_or_starts(tmp_path, monkeypatch):
    path = csv_file(tmp_path, [source_row(**{METRIC_COLUMNS[0]: None})])
    database = tmp_path / "readonly.sqlite3"
    def forbidden(*args, **kwargs):
        pytest.fail("Dry run attempted to start/approve/publish")
    for method in ("decide", "start", "prepare_publication", "complete_offline"):
        monkeypatch.setattr(BatchRegistry, method, forbidden)
    def run(value=100.0):
        return readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", registry_path=database,
            connector=lambda: MockConnection([warehouse_row(**{METRIC_COLUMNS[0]: value})]))
    first, second, changed = run(), run(), run(101.0)
    assert first["batch_id"] == second["batch_id"]
    assert first["batch_id"] != changed["batch_id"]
    assert first["registry_status"] == "WAITING_APPROVAL"
    with BatchRegistry(database) as registry:
        assert len(registry.history(first["batch_id"])) == 1
        revision = registry.revisions(first["batch_id"])[0]
        assert registry.decisions(revision["revision_id"]) == []
        assert registry.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert registry.batch(first["batch_id"])["policy_version"].startswith("READ_ONLY_FLOAT_TEXT_V1:snapshot:")


def test_bad_csv_rejected_before_connection(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("not,the,contract\n1,2,3\n", encoding="utf-8")
    def forbidden():
        pytest.fail("Bad input should fail before connecting")
    with pytest.raises(ValueError):
        readonly_dry_run.dry_run(path, input_mode="QUARTER_DELTA", connector=forbidden)


def test_actual_tra_csv_against_mocked_binary_float_snapshot(tmp_path):
    from telecom_pipeline.readers import read_source
    from telecom_pipeline.incremental import canonicalize_record
    source = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [canonicalize_record(row) for row in read_source(source).data.to_dict(orient="records")]
    rows = [(r.year, r.quarter, *(None if v is None else float(v) for v in r.metrics)) for r in records]
    report = readonly_dry_run.dry_run(source, input_mode="FULL_SNAPSHOT", connector=lambda: MockConnection(rows),
                                      registry_path=tmp_path / "dry-run.sqlite3")
    assert report["counts"]["UNCHANGED"] == 66
    assert report["counts"]["REVISED"] == 0
    assert report["warehouse_rows"] == report["source_rows"] == 66
    assert report["source_sha256"] == before == hashlib.sha256(source.read_bytes()).hexdigest()


def test_cli_without_opt_in_cannot_connect(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("CLI connected without opt-in")
    monkeypatch.setattr(readonly_dry_run, "dry_run", forbidden)
    assert readonly_dry_run.main([str(tmp_path / "not-read.csv"), "--input-mode", "QUARTER_DELTA"]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "LIVE_READ_OPT_IN_REQUIRED"


def test_cli_success_json_with_mocked_connection(tmp_path, monkeypatch, capsys):
    path = csv_file(tmp_path, [source_row()])
    monkeypatch.setattr(readonly_dry_run, "fetch_quarterly_records",
        lambda **kwargs: fetch_quarterly_records(connector=lambda: MockConnection([warehouse_row()])))
    assert readonly_dry_run.main([str(path), "--input-mode", "QUARTER_DELTA", "--live-read-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["UNCHANGED"] == 1


def test_cli_rejected_scope_exit_code(tmp_path, monkeypatch, capsys):
    path = csv_file(tmp_path, [source_row(3)])
    monkeypatch.setattr(readonly_dry_run, "fetch_quarterly_records",
        lambda **kwargs: fetch_quarterly_records(connector=lambda: MockConnection([warehouse_row()])))
    assert readonly_dry_run.main([str(path), "--input-mode", "FULL_SNAPSHOT", "--live-read-only"]) == 2
    assert json.loads(capsys.readouterr().out)["classification_status"] == "REJECTED_SCOPE"


@pytest.mark.parametrize("error,code", [(RuntimeError("DO_NOT_LEAK"), "DRY_RUN_FAILED"),
    (FileNotFoundError("DO_NOT_LEAK"), "LOCAL_FILE_ERROR"),
    (ReadOnlyError("WAREHOUSE_READ_FAILED"), "WAREHOUSE_READ_FAILED"),
    (KeyboardInterrupt(), "INTERRUPTED")])
def test_cli_errors_do_not_print_exception_messages(monkeypatch, capsys, error, code):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(readonly_dry_run, "dry_run", fail)
    assert readonly_dry_run.main(["synthetic.csv", "--input-mode", "QUARTER_DELTA", "--live-read-only"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "DO_NOT_LEAK" not in output.err
    assert json.loads(output.err)["error_code"] == code
