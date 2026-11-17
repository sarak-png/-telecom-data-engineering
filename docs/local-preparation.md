# Local pipeline preparation — 2026-11-09

After Google/Microsoft OAuth configuration was added, the full offline suite
passed **72 tests**, with one live test skipped (1.85s). The live authentication
attempt timed out; see [current authentication status](archive/phase3-verification.md).

The user manually checked Snowflake and reported that TELECOM_DB is absent.
This is a user-reported finding; the connector has not verified it independently.
**No cloud object creation or modification is authorized.** CUSTOMERS and
CUSTOMER_DETAILS remain untouched.

## Execute in telecom_de

```powershell
conda activate telecom_de
python -m telecom_pipeline.pipeline data/raw/tra-open-data-traffic.csv
python -m telecom_pipeline.transformation --output data/processed/tra-local-v1.json
python -m telecom_pipeline.transformation --output data/processed/tra-local-v1.json
python -m pytest -q
```

The transformation module imports no Snowflake connector. It reuses the existing
CSV/Excel readers and validation, operates on a single snapshot, and writes only
derived local output. Output is excluded from Git.

## Transformation rules

| Source/check | Behavior |
| --- | --- |
| Year `2,026.00` and quarter `2.00` | Required, validated integer values; Pandas Int64 |
| Metric strings | Exact Python Decimal; units unchanged |
| Warehouse precision | Reject overflow or fractional precision beyond NUMBER(38,6); no rounding |
| Missing metric | Retain None/null; never impute zero |
| Metric column names | Uppercase with underscores |
| Quarter date | First day of its calendar quarter |
| Identical period duplicates | Keep first occurrence; count duplicates |
| Conflicting values for same period | Reject entire snapshot |
| Lineage | Source SHA-256 plus first corresponding RAW row hash |

Staging has 11 columns: record hash, source hash, year, quarter, and seven metrics.
Mart has 11: year, quarter, quarter-start date, seven metrics, source hash. No
snapshots are combined and no latest-record policy is invented. Warehouse load
timestamps/batch IDs are left to future warehouse execution, not fabricated here.

The JSON export includes the transformation version, quality/inspection reports,
and both tables. Decimal values serialize as exact strings, dates as ISO dates,
and missing cells as JSON null. In-memory tables retain typed Decimal/date/Int64
values. Original text formatting remains preserved in the raw source.

Identical exports return REUSED. A different existing output raises an error;
choose a new filename rather than overwriting it. Output cannot be placed in the
raw source directory. A process interruption can leave a partial newly created
export; inspect it and use a new output path instead of silently replacing it.

## SQL templates: prepared only

`sql/004_staging_preview.sql` and `sql/005_mart_preview.sql` contain SELECT-only
preview transformations scoped by a bound source SHA-256. The staging preview
requires schema, type, null, precision, and conflicting-period validation first;
numeric conversion alone is not validation. Templates have not been executed or
validated by a live Snowflake SQL engine.

```powershell
python -m telecom_pipeline.snowflake_loader --database TELECOM_DB --render-sql 004_staging_preview.sql
python -m telecom_pipeline.snowflake_loader --database TELECOM_DB --render-sql 005_mart_preview.sql
```

Rendering does not connect. Do not run cloud DDL, MERGE, `--execute-approved`, or
enable the live test during this local preparation stage.

## Actual results

66 source rows produced 66 staging rows and 66 mart rows. All 84 missing metric
cells were preserved; duplicate source rows: 0. Quarter dates range from
2010-01-01 to 2026-04-01. Export returned CREATED, then REUSED on the identical
rerun. Tests: **67 passed, 1 live test skipped in 1.84s**.

Original raw SHA-256 remained:
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.

See [private authentication setup](snowflake-authentication.md) for the next
read-only connection check. No successful authentication is claimed.
