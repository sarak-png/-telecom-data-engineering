# Snowflake read-only dry-run

This opt-in workflow compares an incoming CSV with the confirmed
`TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY` table. It reuses contract 1.1, the
existing environment-based connection configuration, the incremental classifier,
and optionally the local batch registry. Production ingestion and loading entry
points remain unchanged.

## Read boundary

`telecom_pipeline/snowflake_readonly.py` has one fixed, contract-derived SELECT:

```sql
SELECT "YEAR", "QUARTER", "OUTGOING_INT_MIN_M", "INCOMING_INT_MIN_M",
       "MOBILE_LOCAL_MIN_M", "FIXED_LOCAL_MIN_M", "FIXED_BROADBAND_GB_M",
       "MOBILE_BROADBAND_GB_M", "INTERNET_CAPACITY_MBPS"
FROM "TELECOM_DB"."ANALYTICS"."FACT_TELECOM_QUARTERLY"
ORDER BY "YEAR", "QUARTER"
```

The adapter provides no arbitrary SQL execution API, imports no write executor,
and issues no warehouse DDL, DML, commits or rollbacks. Cursor and connection are
closed after reading, including on failures. Connection session settings reuse
the existing timeouts/UTC configuration and set query tag
`telecom_de_readonly_dry_run`. The configured database must match the contract;
the CLI cannot override the table. Missing/misordered result columns, duplicate
business keys, invalid quarters, negative/nonfinite metrics, and unexpected
native value types fail closed. This checks returned columns/values; it does
not replace a future live DESCRIBE verification of every column attribute.

One SELECT retrieves a statement snapshot; the warehouse may change after it
finishes. A dry-run is not a publication checkpoint or freshness guarantee.
The adapter's restricted API is an application boundary, not a server-enforced
read-only session. Use an **existing** role with only the required USAGE/SELECT
access when available. This workflow neither creates a role nor changes grants.
A live SELECT may resume the selected warehouse and consume credits.

## FLOAT comparison policy: FLOAT_TEXT_V1

The connector's returned Python floats are converted with `Decimal(str(value))`.
This stores the short round-trip decimal rendering of the returned binary float.
It does **not** recover the original CSV precision or assert that the warehouse
value was originally that decimal. For example, returned `0.1` becomes
`Decimal('0.1')`, rather than the longer exact binary expansion from
`Decimal.from_float(0.1)`. See Python's [Decimal conversion documentation](https://docs.python.org/3.11/library/decimal.html)
and [float representation documentation](https://docs.python.org/3.11/library/stdtypes.html#float.hex).
Snowflake's [Python Connector documentation](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-example)
describes the default conversion of result values to native Python types.

Incoming CSV metrics retain their canonical source Decimal precision. The
existing classifier compares these with the approximate warehouse representation
using exact Decimal equality. No rounding, epsilon, or provisional four-ULP
tolerance is applied. Therefore:

- Source `100.00` and returned `100.0` compare UNCHANGED.
- Source `0.100000000000000005` and returned `0.1` compare REVISED. This can be
  a precision artifact rather than evidence of a historical correction.
- Different original decimals can map to the same FLOAT. Source `0.1` can
  compare UNCHANGED even if the original loaded decimal had more digits.
- SQL NULL becomes Python `None` and stays NULL; zero is distinct. NULL/value
  transitions are revisions. NaN/infinities and negative values are rejected.

Counts are **diagnostic representation comparisons**, not authoritative evidence
of original-source equality or revisions. Recovering canonical accepted history
requires separately reviewed source/audit evidence. Precision policy and
read-back tolerance remain future integration work.

## Environment and optional manual live test

Use your existing `telecom_de` environment. The Snowflake connector is already
listed in `requirements-snowflake.txt`; this implementation installs nothing.
The project's `.env.example` is reference material and is **not automatically
loaded**. Set connection variables privately in your own PowerShell process:

```powershell
conda activate telecom_de
$env:SNOWFLAKE_ACCOUNT = '<your-account-identifier>'
$env:SNOWFLAKE_USER = '<your-login-username>'
$env:SNOWFLAKE_ROLE = '<an-existing-role-with-SELECT-access>'
$env:SNOWFLAKE_WAREHOUSE = 'SNOWFLAKE_LEARNING_WH'
$env:SNOWFLAKE_DATABASE = 'TELECOM_DB'
$env:SNOWFLAKE_AUTHENTICATOR = 'externalbrowser'
```

The existing connection helper supports browser SSO when the account supports
it, plus its other configured authentication methods. Google/Microsoft sign-in
alone does not prove external-browser SSO is available for this account. Existing
OAuth rules and role restrictions remain unchanged. No authentication or account
policy changes are attempted. No passwords/tokens are accepted as CLI arguments.
Do not paste credentials into commands, audit rationales, or reports, and do not
enable connector DEBUG logging. This code prints no connection options and does
not capture environment variables in SQLite.

From the project root, the **optional manual live read-only test** is:

```powershell
python -m telecom_pipeline.readonly_dry_run data/raw/tra-open-data-traffic.csv --input-mode FULL_SNAPSHOT --live-read-only
```

This command opens a real connection only because `--live-read-only` is present.
No live connection was run during development. Omitting the flag returns a safe
error without connecting. `--help` never connects. A successful authentication
still requires SELECT/USAGE access to the fact table and warehouse.

Future quarter-only CSV input uses:

```powershell
python -m telecom_pipeline.readonly_dry_run <path-to-quarter-delta.csv> --input-mode QUARTER_DELTA --live-read-only
```

Both TRA bilingual CSV exports and plain nine-column CSVs are supported through
the existing reader. Headers must match exactly the RAW or STAGING contract.
Strict UTF-8/BOM decoding is the default; a verified alternative can be passed
with `--encoding`. The same byte snapshot supplies parsed rows and the SHA-256.
Invalid source structure/types fail before connecting. No file is modified.
Full snapshots must include accepted history; deltas may omit prior quarters.
The classifier retains scope, continuity and conflicting-duplicate checks.
There is no fixed 66-row constraint for future input.

## Output and optional persistence

The command emits JSON containing source SHA-256, contract version, table, input
mode, read timestamps, warehouse snapshot fingerprint, comparison-policy label,
input/warehouse row counts, NEW/UNCHANGED/REVISED/CONFLICTING_DUPLICATE counts,
scope/gap diagnostics, and revised periods/metric names. It reports
WAITING_APPROVAL if revisions exist; it never records approval or publishes data.
READY means only that this diagnostic classifier found no hold condition.

Exit codes: `0` means the report was produced (including WAITING_APPROVAL), `2`
means opt-in/CLI validation failed or the plan was REJECTED_SCOPE/REJECTED_CONFLICT,
and `1` means input, configuration, connection, local registry or other runtime
failure. Runtime failures produce constant error codes on stderr, without
connector exception messages, credentials, tracebacks or account details.
Examples include `CONNECTION_CONFIGURATION_INVALID`,
`CONFIGURED_DATABASE_MISMATCH`, `WAREHOUSE_READ_FAILED`,
`WAREHOUSE_COLUMN_MISMATCH`, and `DUPLICATE_WAREHOUSE_PERIOD`.

Persistence is disabled by default. To register diagnostic plans, use a
**dedicated local, non-synchronized registry** separate from future publication
state. For example:

```powershell
$dryRunRegistry = Join-Path $env:LOCALAPPDATA 'telecom_de/control/readonly-dry-runs.sqlite3'
python -m telecom_pipeline.readonly_dry_run data/raw/tra-open-data-traffic.csv --input-mode FULL_SNAPSHOT --live-read-only --registry $dryRunRegistry
```

The workflow calls only `BatchRegistry.register()`: no attempts, approvals,
publication guards, or completions are invoked. New rows become PENDING;
revisions WAITING_APPROVAL; rejected plans FAILED. These statuses describe the
diagnostic plan only. Do not approve or promote diagnostic plans for publication.
They contain approximate accepted values, not canonical accepted source history.

The registry policy-context string is
`READ_ONLY_FLOAT_TEXT_V1:snapshot:<warehouse-snapshot-sha256>`.
This intentionally uses the existing policy-version identity field to bind the
comparison policy **and observation context** without migrating the registry.
An identical file/mode/scope and warehouse snapshot reuse the same batch ID;
a changed warehouse snapshot gets a separate diagnostic identity. Read timestamps
are report observations, while repeated registration leaves the original
durable plan unchanged. An approval from an exact-source offline plan cannot be
reused under this distinct context. Existing local registry APIs remain unchanged.

## Investigating WAREHOUSE_READ_FAILED

The adapter previously converted every unexpected exception from connector
import, connection setup, cursor creation, SELECT execution, result fetching,
or conversion into `WAREHOUSE_READ_FAILED`. The original response alone cannot
establish which stage failed. Configured environment variables can still be
followed by authentication, authorization, network or client compatibility errors.

Run this command manually from the project root in your configured terminal:

```powershell
conda activate telecom_de
python -m telecom_pipeline.readonly_dry_run data/raw/tra-open-data-traffic.csv --input-mode FULL_SNAPSHOT --live-read-only --diagnostics
```

`--diagnostics` adds a sanitized `diagnostics` object to error JSON. It contains
the failed stage, probable category, classification basis, allowlisted connector
error number and SQLSTATE, plus a fixed troubleshooting hint. The flag preserves
the existing SELECT and live opt-in requirements. Its output uses static labels
and explicitly recognized protocol codes. Raw messages, exception class names,
account identifiers, usernames, hosts, tokens, query/request IDs and connection
options are excluded. Diagnostic output is emitted to stderr and is not persisted
in the batch registry. The default error response retains its existing shape.

| Category | Possible cause and next private check |
| --- | --- |
| AUTHENTICATION | Browser SSO, identity-provider authentication or login policy. Check the configured sign-in method and completion of the browser flow. |
| AUTHORIZATION | Required USAGE/SELECT privileges for the existing role. Check access in your own Snowflake session. |
| WAREHOUSE_ACCESS | Missing/inaccessible warehouse, unavailable warehouse or no active warehouse. Check the configured warehouse and role's USAGE access. |
| OBJECT_ACCESS_OR_EXISTENCE | The selected object may be missing or inaccessible. Check the object and role privileges. |
| CONNECTION | Authentication or network/proxy/TLS failure remains possible. Check the existing login method and local connectivity. |
| TIMEOUT | Browser login, network or query timed out. Use the stage to locate the operation. |
| SQL_EXECUTION | Failure during the fixed SELECT. Inspect private query history if the request reached Snowflake. |
| RESULT_FETCH | Failure while fetching results. Check result-download connectivity and connector compatibility. |
| RESULT_VALIDATION | Returned columns, period keys or metric values failed validation, or conversion failed. Check the warehouse contract. |
| CONFIGURATION / CLIENT_CONFIGURATION / DEPENDENCY / CLIENT_ERROR | Local settings, unsupported connection options, missing connector or cursor/client failure. Check the dedicated environment and the reported stage. |

Stages are `CONFIGURATION`, `IMPORT_CONNECTOR`, `CONNECT`, `OPEN_CURSOR`,
`EXECUTE_SELECT`, `READ_METADATA`, `FETCH_ROWS` and `CONVERT_ROWS`.
`classification_basis` reports `ERROR_CODE`, `MESSAGE_PATTERN`, `EXCEPTION_TYPE`
or `STAGE`. Message patterns are evaluated only in memory to select a static
category. They provide a probable explanation, so the stage and basis should be
considered together. Unknown protocol codes become JSON null.

An `08001`/`250001` connection failure can cover multiple causes. It remains
CONNECTION unless a more specific recognized signal is available. Snowflake
also uses `002003` for object-access/existence errors, which can reflect a missing
object or insufficient access. The adapter retains that ambiguity. See
[Snowflake's connection example](https://www.snowflake.com/en/developers/guides/getting-started-with-snowflake-cli/)
and [object error documentation](https://docs.snowflake.com/en/release-notes/bcr-bundles/un-bundled/bcr-1858).

For example, a mocked insufficient-privilege failure produces this safe output:

```json
{
  "status": "ERROR",
  "error_code": "WAREHOUSE_READ_FAILED",
  "diagnostics": {
    "stage": "EXECUTE_SELECT",
    "category": "AUTHORIZATION",
    "classification_basis": "ERROR_CODE",
    "connector_errno": null,
    "sqlstate": "42501",
    "hint": "Check that the existing role has the required USAGE and SELECT privileges."
  }
}
```

The specific cause of the reported live failure remains pending the next manual
diagnostic result. Investigation and testing for this change use local files
and mocked failures.

Verified on 2026-11-16 using `telecom_de`. The 29 new diagnostic tests plus the
50 existing read-only tests passed together. The complete offline suite reported
**273 passed in 3.22 seconds** with the live test explicitly excluded. CLI help
includes `--diagnostics`. Tests cover stage/category selection, ambiguous errors,
redaction of sensitive sentinels and unknown metadata, resource cleanup, and
preservation of the explicit live opt-in requirement.

## Local tests

```powershell
conda activate telecom_de
$readOnlyTestTemp = Join-Path (Get-Location) ('data/ingestion/readonly-tests-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests/test_snowflake_readonly.py -q -p no:cacheprovider --basetemp $readOnlyTestTemp
```

Tests inject mock connections, including for the live-code branch and CLI.
They assert the sole statement is the fixed SELECT, validate cleanup and safe
errors, test precision ambiguity/NULL transitions, and prevent approval/start/
publication calls. The actual original TRA CSV is also compared against a
**mocked** binary-FLOAT projection of its own values. That test is an offline
round-trip check, not a successful live Snowflake reconciliation.

For broader offline regression, exclude the existing live test explicitly:

```powershell
$offlineTestTemp = Join-Path (Get-Location) ('data/ingestion/offline-tests-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests --ignore=tests/test_snowflake_live.py -q -p no:cacheprovider --basetemp $offlineTestTemp
```

Verified locally on 2026-11-15 in `telecom_de`: **50 new integration tests
passed**, and the complete offline suite reported **244 passed in 4.04 seconds**.
The actual CSV versus mocked FLOAT snapshot test reported 66 UNCHANGED records.
Fingerprints of all 36 pre-existing files under `telecom_pipeline`, `tests`,
`sql`, `config` and `data/raw` remained unchanged. The original CSV SHA-256 remains
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
CLI help was exercised without connecting. No live Snowflake authentication,
SELECT, or warehouse reconciliation was performed.
