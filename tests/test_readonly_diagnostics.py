"""Synthetic failures exercise sanitized CLI diagnostics without network access."""
import json
from types import SimpleNamespace

import pytest

from telecom_pipeline import readonly_dry_run, snowflake_readonly
from telecom_pipeline.snowflake_readonly import ReadOnlyError, SELECT_QUARTERLY, fetch_quarterly_records


SENSITIVE = "secret-token private-account private-user https://private-host/?token=secret-token"


class ProtocolFailure(Exception):
    def __init__(self, message=SENSITIVE, *, errno=None, sqlstate=None):
        super().__init__(message)
        self.errno = errno
        self.sqlstate = sqlstate
        self.sfqid = SENSITIVE
        self.query = SENSITIVE
        self.request_id = SENSITIVE


def fail_at(stage, error):
    """Return a reader that raises at a chosen stage and records cleanup."""
    state = SimpleNamespace(cursor_closed=False, connection_closed=False, statements=[])

    class Cursor:
        description = None

        def execute(self, sql):
            state.statements.append(sql)
            assert sql == SELECT_QUARTERLY
            if stage == "EXECUTE_SELECT":
                raise error

        def fetchall(self):
            raise error

        def close(self):
            state.cursor_closed = True

    class Connection:
        def cursor(self):
            if stage == "OPEN_CURSOR":
                raise error
            cursor = Cursor()
            if stage != "READ_METADATA":
                cursor.description = [(name,) for name in snowflake_readonly.READ_COLUMNS]
            return cursor

        def close(self):
            state.connection_closed = True

    def connector():
        if stage == "CONNECT":
            raise error
        return Connection()

    return connector, state


@pytest.mark.parametrize("stage,error,category,basis", [
    ("CONNECT", ProtocolFailure(errno=390100), "AUTHENTICATION", "ERROR_CODE"),
    ("CONNECT", ProtocolFailure(sqlstate="28000"), "AUTHENTICATION", "ERROR_CODE"),
    ("CONNECT", ProtocolFailure("Authentication failed " + SENSITIVE), "AUTHENTICATION", "MESSAGE_PATTERN"),
    ("CONNECT", ProtocolFailure("Identity provider SAML failure " + SENSITIVE), "AUTHENTICATION", "MESSAGE_PATTERN"),
    ("CONNECT", ProtocolFailure(errno=250001, sqlstate="08001"), "CONNECTION", "ERROR_CODE"),
    ("CONNECT", ProtocolFailure(sqlstate="08006"), "CONNECTION", "ERROR_CODE"),
    ("CONNECT", ProtocolFailure(), "CONNECTION", "STAGE"),
    ("CONNECT", ProtocolFailure(sqlstate="HYT00"), "TIMEOUT", "ERROR_CODE"),
    ("CONNECT", TimeoutError(SENSITIVE), "TIMEOUT", "EXCEPTION_TYPE"),
    ("CONNECT", TypeError(SENSITIVE), "CLIENT_CONFIGURATION", "EXCEPTION_TYPE"),
    ("CONNECT", ProtocolFailure(errno=251007), "CLIENT_CONFIGURATION", "ERROR_CODE"),
    ("OPEN_CURSOR", ProtocolFailure(sqlstate="08003"), "CONNECTION", "ERROR_CODE"),
    ("EXECUTE_SELECT", ProtocolFailure(sqlstate="42501"), "AUTHORIZATION", "ERROR_CODE"),
    ("EXECUTE_SELECT", ProtocolFailure("Insufficient privileges " + SENSITIVE), "AUTHORIZATION", "MESSAGE_PATTERN"),
    ("EXECUTE_SELECT", ProtocolFailure("No active warehouse selected " + SENSITIVE), "WAREHOUSE_ACCESS", "MESSAGE_PATTERN"),
    ("EXECUTE_SELECT", ProtocolFailure("Warehouse 'private' does not exist or not authorized " + SENSITIVE, errno=2003), "WAREHOUSE_ACCESS", "MESSAGE_PATTERN"),
    ("EXECUTE_SELECT", ProtocolFailure(errno=2003, sqlstate="42S02"), "OBJECT_ACCESS_OR_EXISTENCE", "ERROR_CODE"),
    ("EXECUTE_SELECT", ProtocolFailure(sqlstate="42000"), "SQL_EXECUTION", "STAGE"),
    ("FETCH_ROWS", ProtocolFailure(sqlstate="08S01"), "CONNECTION", "ERROR_CODE"),
    ("FETCH_ROWS", ProtocolFailure(), "RESULT_FETCH", "STAGE"),
])
def test_failure_category_and_stage_are_safe(stage, error, category, basis):
    connector, resources = fail_at(stage, error)
    with pytest.raises(ReadOnlyError) as caught:
        fetch_quarterly_records(connector=connector)
    diagnostic = caught.value.diagnostics
    assert diagnostic["stage"] == stage
    assert diagnostic["category"] == category
    assert diagnostic["classification_basis"] == basis
    assert set(diagnostic) == {"stage", "category", "classification_basis", "connector_errno", "sqlstate", "hint"}
    serialized = json.dumps(diagnostic)
    assert all(value not in serialized for value in SENSITIVE.split())
    if stage != "CONNECT":
        assert resources.connection_closed
    if stage not in ("CONNECT", "OPEN_CURSOR"):
        assert resources.cursor_closed


@pytest.mark.parametrize("number,state", [("secret-token", "private-user"),
    (123456789, "ALICE"), (True, "08001-secret-token"), (None, None)])
def test_unrecognized_metadata_is_omitted(number, state):
    connector, _ = fail_at("CONNECT", ProtocolFailure(errno=number, sqlstate=state))
    with pytest.raises(ReadOnlyError) as caught:
        fetch_quarterly_records(connector=connector)
    diagnostic = caught.value.diagnostics
    assert diagnostic["connector_errno"] is None
    assert diagnostic["sqlstate"] is None


def test_metadata_validation_is_distinguished_from_select_failure():
    connector, resources = fail_at("READ_METADATA", ProtocolFailure())
    with pytest.raises(ReadOnlyError) as caught:
        fetch_quarterly_records(connector=connector)
    assert caught.value.diagnostics["stage"] == "READ_METADATA"
    assert caught.value.diagnostics["category"] == "RESULT_VALIDATION"
    assert resources.cursor_closed and resources.connection_closed


def test_configuration_failure_has_safe_stage_and_hint():
    with pytest.raises(ReadOnlyError) as caught:
        fetch_quarterly_records(allow_live=True, environ={})
    assert str(caught.value) == "CONNECTION_CONFIGURATION_INVALID"
    assert caught.value.diagnostics["stage"] == "CONFIGURATION"


@pytest.mark.parametrize("enabled", [True, False])
def test_cli_diagnostic_flag_controls_safe_error_output(monkeypatch, capsys, enabled):
    def fail(*args, **kwargs):
        connector, _ = fail_at("EXECUTE_SELECT", ProtocolFailure(sqlstate="42501"))
        return fetch_quarterly_records(connector=connector)
    monkeypatch.setattr(readonly_dry_run, "dry_run", fail)
    arguments = ["synthetic.csv", "--input-mode", "QUARTER_DELTA", "--live-read-only"]
    if enabled:
        arguments.append("--diagnostics")
    assert readonly_dry_run.main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    result = json.loads(captured.err)
    assert result["error_code"] == "WAREHOUSE_READ_FAILED"
    if enabled:
        assert result["diagnostics"]["category"] == "AUTHORIZATION"
    else:
        assert result == {"status": "ERROR", "error_code": "WAREHOUSE_READ_FAILED"}
    assert all(value not in captured.err for value in SENSITIVE.split())


def test_diagnostic_flag_preserves_live_opt_in(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Connection attempted without explicit live opt-in")
    monkeypatch.setattr(readonly_dry_run, "dry_run", forbidden)
    assert readonly_dry_run.main(["synthetic.csv", "--input-mode", "QUARTER_DELTA", "--diagnostics"]) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "LIVE_READ_OPT_IN_REQUIRED"
