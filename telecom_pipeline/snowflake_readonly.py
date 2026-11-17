"""Opt-in, fixed-SELECT Snowflake adapter. No warehouse write dependencies.

FLOAT_TEXT_V1 uses Decimal(str(python_float)): a shortest round-trip decimal
rendering of the returned binary float, NOT recovered original source precision.
No ULP tolerance or source-precision equality guarantee is supplied.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math

from .incremental import CanonicalRecord, KEY_COLUMNS, METRIC_COLUMNS
from .snowflake_config import WAREHOUSE_CONTRACT, connection_options, identifier


COMPARISON_VERSION = "FLOAT_TEXT_V1"
READ_COLUMNS = KEY_COLUMNS + METRIC_COLUMNS
_fact = WAREHOUSE_CONTRACT["objects"]["quarterly_fact"]
TABLE = ".".join(identifier(part) for part in
                 (WAREHOUSE_CONTRACT["database"], _fact["schema"], _fact["name"]))
SELECT_QUARTERLY = ("SELECT " + ", ".join(f'"{identifier(name)}"' for name in READ_COLUMNS)
                    + " FROM " + ".".join(f'"{part}"' for part in TABLE.split("."))
                    + ' ORDER BY "YEAR", "QUARTER"')


class ReadOnlyError(RuntimeError):
    """A safe, constant error code; never includes connector exception messages."""

    def __init__(self, code, *, diagnostics=None):
        super().__init__(code)
        self.diagnostics = diagnostics


def _safe_diagnostics(error, stage):
    """Return only static labels/hints and explicitly allowlisted protocol codes.

    Message patterns select a probable category in memory. Their matched text,
    exception type, account details, query IDs and raw messages are never copied.
    """
    known_states = {"08001", "08003", "08004", "08006", "08S01", "28000",
                    "42501", "42000", "42S02", "42S22", "02000", "57014", "HYT00", "HYT01"}
    known_numbers = {2003, 250001, 250002, 251001, 251005, 251006, 251007,
                     251017, 252005, 252006, 255001, 255002, 255006, 390100}
    number = getattr(error, "errno", None)
    number = number if type(number) is int and number in known_numbers else None
    state = getattr(error, "sqlstate", None)
    state = state if type(state) is str and state in known_states else None
    try:
        message = str(error).casefold()
    except Exception:
        message = ""

    basis = "STAGE"
    if stage == "CONFIGURATION":
        category = "CONFIGURATION"
    elif stage == "IMPORT_CONNECTOR":
        category = "DEPENDENCY"
    elif isinstance(error, ReadOnlyError) or stage in ("READ_METADATA", "CONVERT_ROWS"):
        category = "RESULT_VALIDATION"
    elif stage == "CONNECT" and (isinstance(error, TypeError) or
                                number in (251001, 251005, 251006, 251007, 251017)):
        category, basis = "CLIENT_CONFIGURATION", "EXCEPTION_TYPE" if number is None else "ERROR_CODE"
    elif state == "28000" or number == 390100:
        category, basis = "AUTHENTICATION", "ERROR_CODE"
    elif "warehouse" in message and any(text in message for text in (
            "no active warehouse", "not authorized", "does not exist", "insufficient privileges",
            "suspended", "cannot be resumed")):
        category, basis = "WAREHOUSE_ACCESS", "MESSAGE_PATTERN"
    elif state == "42501":
        category, basis = "AUTHORIZATION", "ERROR_CODE"
    elif any(text in message for text in ("authentication failed", "failed to authenticate",
             "incorrect username or password", "identity provider", "saml", "oauth token")):
        category, basis = "AUTHENTICATION", "MESSAGE_PATTERN"
    elif "insufficient privileges" in message or "access control error" in message:
        category, basis = "AUTHORIZATION", "MESSAGE_PATTERN"
    elif number == 2003 or state == "42S02":
        category, basis = "OBJECT_ACCESS_OR_EXISTENCE", "ERROR_CODE"
    elif state in ("HYT00", "HYT01", "57014") or isinstance(error, TimeoutError):
        category, basis = "TIMEOUT", "ERROR_CODE" if state else "EXCEPTION_TYPE"
    elif state in ("08001", "08003", "08004", "08006", "08S01") or number in (250001, 250002):
        category, basis = "CONNECTION", "ERROR_CODE"
    elif stage == "CONNECT":
        category = "CONNECTION"
    elif stage == "EXECUTE_SELECT":
        category = "SQL_EXECUTION"
    elif stage == "FETCH_ROWS":
        category = "RESULT_FETCH"
    else:
        category = "CLIENT_ERROR"

    hints = {
        "CONFIGURATION": "Check required environment variables and the configured authentication method privately.",
        "DEPENDENCY": "Check the Snowflake connector installation in telecom_de.",
        "CLIENT_CONFIGURATION": "Check connector compatibility and accepted connection options in telecom_de.",
        "AUTHENTICATION": "Check the existing sign-in method, browser SSO completion and account authentication policy.",
        "AUTHORIZATION": "Check that the existing role has the required USAGE and SELECT privileges.",
        "WAREHOUSE_ACCESS": "Check the configured warehouse exists, is available and permits USAGE for the existing role.",
        "OBJECT_ACCESS_OR_EXISTENCE": "Check object existence and role privileges. This error can represent either condition.",
        "TIMEOUT": "Check browser sign-in completion, network availability and warehouse query availability.",
        "CONNECTION": "Check authentication, browser SSO, network, proxy and TLS connectivity. The cause remains ambiguous.",
        "SQL_EXECUTION": "Inspect the failed SELECT in your private Snowflake query history if it was submitted.",
        "RESULT_FETCH": "Check result-download connectivity and connector compatibility.",
        "RESULT_VALIDATION": "Check returned column names, types, quarterly keys and metric values against the contract.",
        "CLIENT_ERROR": "Check connector compatibility and the reported client stage.",
    }
    return {"stage": stage, "category": category, "classification_basis": basis,
            "connector_errno": number, "sqlstate": state, "hint": hints[category]}


@dataclass(frozen=True)
class WarehouseSnapshot:
    records: tuple[CanonicalRecord, ...]
    started_at: str
    finished_at: str

    @property
    def sha256(self):
        payload = {"table": TABLE, "comparison_version": COMPARISON_VERSION,
                   "records": sorted((record.year, record.quarter, record.payload_hash)
                                     for record in self.records)}
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def float_comparison_value(value):
    """Preserve NULL, reject unexpected/nonfinite/negative metrics, label elsewhere."""
    if value is None:
        return None
    if not isinstance(value, float) or not math.isfinite(value) or value < 0:
        raise ReadOnlyError("INVALID_WAREHOUSE_METRIC")
    return Decimal(str(value))


def _convert(rows):
    records, keys = [], set()
    for row in rows:
        if len(row) != len(READ_COLUMNS):
            raise ReadOnlyError("WAREHOUSE_COLUMN_MISMATCH")
        year, quarter = row[:2]
        if any(isinstance(value, bool) or not isinstance(value, (int, Decimal)) for value in (year, quarter)):
            raise ReadOnlyError("INVALID_WAREHOUSE_PERIOD")
        try:
            record = CanonicalRecord(year, quarter, tuple(float_comparison_value(v) for v in row[2:]))
        except ValueError:
            raise ReadOnlyError("INVALID_WAREHOUSE_PERIOD") from None
        if record.key in keys:
            raise ReadOnlyError("DUPLICATE_WAREHOUSE_PERIOD")
        keys.add(record.key)
        records.append(record)
    return tuple(sorted(records, key=lambda record: record.key))


def fetch_quarterly_records(*, connector=None, allow_live=False, environ=None):
    """Read one statement snapshot, closing cursor and connection on all paths.

    Inject a zero-argument connector for tests. Without injection, live access
    requires explicit allow_live=True. There is no general execute/query API.
    """
    if connector is None and not allow_live:
        raise ReadOnlyError("LIVE_READ_OPT_IN_REQUIRED")
    conn = cursor = None
    options = None
    stage = "CONFIGURATION"
    started = datetime.now(timezone.utc).isoformat()
    try:
        if connector is None:
            try:
                options = connection_options(environ)
            except (ValueError, KeyError):
                raise ReadOnlyError("CONNECTION_CONFIGURATION_INVALID") from None
            if options["database"] != WAREHOUSE_CONTRACT["database"]:
                raise ReadOnlyError("CONFIGURED_DATABASE_MISMATCH")
            options["session_parameters"] = {
                **options["session_parameters"], "QUERY_TAG": "telecom_de_readonly_dry_run",
            }
            stage = "IMPORT_CONNECTOR"
            import snowflake.connector

            stage = "CONNECT"
            conn = snowflake.connector.connect(**options)
        else:
            stage = "CONNECT"
            conn = connector()
        stage = "OPEN_CURSOR"
        cursor = conn.cursor()
        stage = "EXECUTE_SELECT"
        cursor.execute(SELECT_QUARTERLY)
        stage = "READ_METADATA"
        names = tuple(column[0] for column in cursor.description)
        if names != READ_COLUMNS:
            raise ReadOnlyError("WAREHOUSE_COLUMN_MISMATCH")
        stage = "FETCH_ROWS"
        rows = cursor.fetchall()
        stage = "CONVERT_ROWS"
        records = _convert(rows)
        return WarehouseSnapshot(records, started, datetime.now(timezone.utc).isoformat())
    except ReadOnlyError as error:
        error.diagnostics = _safe_diagnostics(error, stage)
        raise
    except Exception as error:
        raise ReadOnlyError("WAREHOUSE_READ_FAILED", diagnostics=_safe_diagnostics(error, stage)) from None
    finally:
        # Cleanup errors neither expose credentials nor mask the primary failure.
        for resource in (cursor, conn):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if options is not None:
            options.clear()
