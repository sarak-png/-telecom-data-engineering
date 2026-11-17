"""Offline checks for the historical RAW/OPS executor and shared connection helpers.

TELECOM_DE is a synthetic database identifier in the legacy executor tests.
The confirmed five-object mapping is checked in test_warehouse_contract.py.
"""
import copy
import json
from pathlib import Path

import pytest

from telecom_pipeline.snowflake_config import connection_options, identifier, project_database
from telecom_pipeline.snowflake_check import database_exists
from telecom_pipeline.snowflake_loader import (
    OPS_COLUMNS, RAW_COLUMNS, load, local_writer_lock, prepare, render_sql, verify_rows,
)
from telecom_pipeline.validation import COLUMNS


SOURCE = Path(__file__).resolve().parents[1] / "data/raw/tra-open-data-traffic.csv"


def settings():
    return {f"SNOWFLAKE_{key}": value for key, value in {
        "ACCOUNT": "example-account", "USER": "example-user", "ROLE": "LOADER_ROLE",
        "WAREHOUSE": "EXISTING_WH", "DATABASE": "TELECOM_DE", "AUTHENTICATOR": "externalbrowser",
    }.items()}


def test_environment_authentication():
    env = settings()
    assert connection_options(env)["authenticator"] == "externalbrowser"
    assert "password" not in connection_options(env)
    assert connection_options(env)["client_store_temporary_credential"] is False
    env.update(SNOWFLAKE_AUTHENTICATOR="snowflake", SNOWFLAKE_PASSWORD="synthetic-test-secret")
    assert connection_options(env)["password"] == "synthetic-test-secret"
    env.update(SNOWFLAKE_AUTHENTICATOR="SNOWFLAKE_JWT", SNOWFLAKE_PRIVATE_KEY_FILE="example.p8",
               SNOWFLAKE_PRIVATE_KEY_PASSWORD="synthetic-passphrase")
    options = connection_options(env)
    assert options["private_key_password"] == "synthetic-passphrase"
    assert "password" not in options


@pytest.mark.parametrize("auth", ["snowflake", "SNOWFLAKE_JWT", "unsupported"])
def test_missing_auth_credentials(auth):
    env = settings()
    env["SNOWFLAKE_AUTHENTICATOR"] = auth
    with pytest.raises(ValueError):
        connection_options(env)


def test_missing_environment():
    with pytest.raises(ValueError, match="SNOWFLAKE_ACCOUNT"):
        connection_options({})


@pytest.mark.parametrize("value", ["DB; DROP DATABASE X", "DB.NAME", '"Mixed Case"', "", "x--"])
def test_identifier_rejects_sql_injection(value):
    with pytest.raises(ValueError):
        identifier(value)


def test_real_source_plan_and_immutable_bytes():
    before = SOURCE.read_bytes()
    first, rows = prepare(SOURCE)
    second, again = prepare(SOURCE)
    assert first["expected_rows"] == 66
    assert first["batch_id"] != second["batch_id"]
    assert [r["record_hash"] for r in rows] == [r["record_hash"] for r in again]
    assert rows[0]["values"][0] == "2,026.00"
    assert rows[-1]["values"][-1] is None
    assert sum(value is None for row in rows for value in row["values"]) == 84
    assert before == SOURCE.read_bytes()


def as_row(record):
    return (record["record_hash"], record["source_sha256"], record["row_number"], *record["values"])


def test_integrity_validation_rejects_corruption():
    _, records = prepare(SOURCE)
    actual = [as_row(record) for record in records]
    verify_rows(actual, records)
    for broken in (actual[:-1], actual + actual[:1], [(*actual[0][:-1], "wrong")] + actual[1:]):
        with pytest.raises(ValueError):
            verify_rows(broken, records)


class FakeServer:
    """Minimal transaction fake, deliberately not a SQL engine."""
    def __init__(self):
        self.rows = []
        self.executed = []
        self.connections = []
        self.fail_merge = False
        self.corrupt = False
        self.incompatible = False

    def connect(self):
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn


class FakeConnection:
    def __init__(self, server):
        self.server = server
        self.pending = None
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.server.rows = self.pending
        self.pending = None
        self.commits += 1

    def rollback(self):
        self.pending = None
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.conn = connection
        self.result = []

    def execute(self, sql, params=None):
        server = self.conn.server
        server.executed.append((sql, params))
        if "LIMIT 0" in sql:
            names = RAW_COLUMNS if ".RAW." in sql else OPS_COLUMNS
            self.description = [(name,) for name in (names[:-1] if server.incompatible else names)]
        elif sql == "BEGIN":
            self.conn.pending = copy.deepcopy(server.rows)
        elif "SELECT COUNT(*)" in sql:
            self.result = [(sum(row[1] == params[0] for row in self.conn.pending),)]
        elif "MERGE INTO" in sql:
            if server.fail_merge:
                raise RuntimeError("synthetic secret must not be logged")
            existing = {row[0] for row in self.conn.pending}
            self.conn.pending.extend(as_row(row) for row in json.loads(params[0]) if row["record_hash"] not in existing)
        elif "SELECT RECORD_HASH" in sql:
            self.result = [row for row in self.conn.pending if row[1] == params[0]]
            if server.corrupt:
                self.result = self.result[:-1]
        return self

    def fetchone(self):
        return self.result[0]

    def fetchall(self):
        return self.result

    def close(self):
        pass


def test_cloud_writes_require_approval():
    report, rows = prepare(SOURCE)
    server = FakeServer()
    with pytest.raises(PermissionError):
        load(report, rows, database="TELECOM_DE", connector=server.connect)
    assert not server.connections


def test_mocked_load_and_rerun():
    server = FakeServer()
    report, rows = prepare(SOURCE)
    assert load(report, rows, database="TELECOM_DE", approved=True, connector=server.connect)["inserted_rows"] == 66
    report, rows = prepare(SOURCE)
    rerun = load(report, rows, database="TELECOM_DE", approved=True, connector=server.connect)
    assert rerun["inserted_rows"] == 0
    assert rerun["verified_rows"] == 66
    assert len(server.rows) == 66
    assert all(conn.closed and conn.commits == 1 for conn in server.connections)
    assert all(not sql.lstrip().startswith("CREATE") for sql, _ in server.executed)


@pytest.mark.parametrize("failure", ["fail_merge", "corrupt", "incompatible"])
def test_mocked_rollback_and_redacted_errors(failure, caplog):
    server = FakeServer()
    setattr(server, failure, True)
    report, rows = prepare(SOURCE)
    with pytest.raises((RuntimeError, ValueError)):
        load(report, rows, database="TELECOM_DE", approved=True, connector=server.connect)
    assert not server.rows
    assert server.connections[0].closed
    assert server.connections[0].rollbacks == 1
    assert "synthetic secret" not in caplog.text
    if failure == "incompatible":
        assert not any(sql.startswith("INSERT") for sql, _ in server.executed)


def test_local_writer_exclusion_and_recovery(tmp_path):
    with local_writer_lock(tmp_path, "TELECOM_DE"):
        with pytest.raises(RuntimeError):
            with local_writer_lock(tmp_path, "TELECOM_DE"):
                pass
    assert not list(tmp_path.glob("*.lock"))
    with local_writer_lock(tmp_path, "TELECOM_DE"):
        pass


def test_sql_contracts_are_non_destructive():
    sql = render_sql("001_bootstrap.sql", "telecom_de")
    assert "CREATE DATABASE IF NOT EXISTS TELECOM_DE" in sql
    assert "CREATE OR REPLACE" not in sql
    assert "DROP " not in sql
    assert all(f'"{column}" VARCHAR' in sql for column in COLUMNS)
    merge = render_sql("002_merge_raw.sql", "TELECOM_DE")
    assert "WHEN MATCHED" not in merge
    assert merge.count("IS_NULL_VALUE") == 9


def test_discovery_connection_omits_missing_database_context():
    env = settings()
    env["SNOWFLAKE_DATABASE"] = "TELECOM_DB"
    options = connection_options(env, include_database=False)
    assert "database" not in options
    assert options["authenticator"] == "externalbrowser"


@pytest.mark.parametrize("target", ["CUSTOMERS", "customers"])
def test_customers_database_is_protected(target):
    with pytest.raises(ValueError, match="protected"):
        project_database(target)
    with pytest.raises(ValueError):
        render_sql("001_bootstrap.sql", target)
    report, rows = prepare(SOURCE)
    server = FakeServer()
    with pytest.raises(ValueError):
        load(report, rows, database=target, approved=True, connector=server.connect)
    assert not server.connections


@pytest.mark.parametrize("names,expected", [([], False), (["TELECOMXDB"], False),
                                           (["TELECOM_DB"], True)])
def test_read_only_exact_database_lookup(names, expected):
    class Cursor:
        description = [("created_on",), ("name",)]

        def execute(self, sql, params):
            assert sql == "SHOW DATABASES LIKE %s"
            assert params == ("TELECOM_DB",)

        def fetchall(self):
            return [(None, name) for name in names]

        def close(self):
            pass

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    conn = Connection()
    assert database_exists("TELECOM_DB", connector=lambda: conn) is expected
    assert conn.closed


def test_checker_error_diagnostics_exclude_exception_secrets(monkeypatch, capsys):
    from telecom_pipeline import snowflake_check

    class AuthenticationError(Exception):
        errno = 250001
        sqlstate = "08001"

    def broken(*args, **kwargs):
        raise AuthenticationError("SAML identity provider rejected synthetic-secret-token")

    monkeypatch.setattr(snowflake_check, "connection_options", lambda **kwargs: {})
    monkeypatch.setattr(snowflake_check, "database_exists", broken)
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "TELECOM_DB")
    assert snowflake_check.main() == 1
    output = capsys.readouterr().err
    assert "synthetic-secret-token" not in output
    assert json.loads(output)["errno"] == 250001
