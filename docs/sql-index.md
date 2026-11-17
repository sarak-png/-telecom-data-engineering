# SQL script index

All twelve SQL scripts retain their existing paths and contents. This index
classifies their purpose from the statements and warehouse references in each
file. Numbering within the directory does not define a single installation or
execution sequence.

The confirmed architecture is RAW.TRA_TRAFFIC_SOURCE →
STAGING.TRA_TRAFFIC_CLEAN → ANALYTICS.FACT_TELECOM_QUARTERLY and its views,
within TELECOM_DB. Exact types and evidence are recorded in the
[warehouse contract](warehouse-contract.md) and
[contract JSON](../config/warehouse_contract.json).

## Current manual warehouse object scripts

These files describe objects in the confirmed architecture. Each remains subject
to inspection of the existing objects and approval before any manual DDL.

| Script | Statements and purpose | Review scope |
| --- | --- | --- |
| [000_create_telecom_db.sql](../sql/000_create_telecom_db.sql) | CREATE DATABASE IF NOT EXISTS TELECOM_DB | Database setup only. Existing database presence is recorded separately; this file supplies no current-schema migration. |
| [006_create_raw_tra_traffic_source.sql](../sql/006_create_raw_tra_traffic_source.sql) | CREATE TABLE for the nine original source columns as nullable VARCHAR | Source-only RAW landing definition. CREATE fails on an existing table. |
| [analytics/002_create_fact.sql](../sql/analytics/002_create_fact.sql) | CREATE TABLE for the thirteen-column Analytics fact | Includes YEAR/QUARTER, quarter date, seven nullable FLOAT metrics and lineage. CREATE fails on an existing table. |
| [analytics/004_create_views.sql](../sql/analytics/004_create_views.sql) | CREATE VIEW for yearly summary and data coverage | Coverage, completeness and Q4 capacity projections. Existing view names cause CREATE to fail. |

The supplied twelve scripts do not contain a dedicated current-contract
CREATE/load transformation for STAGING.TRA_TRAFFIC_CLEAN. Its schema is recorded
from the supplied DESCRIBE evidence. The legacy STAGING scripts below refer to a
different object and schema. Review that reproducibility gap before preparing a
future warehouse deployment package.

## Historical snapshot checks and loading

These files belong to the validated historical snapshot workflow. In its original
manual review sequence, preflight preceded fact creation/loading, view creation
and reconciliation. Existing object state and current source content determine
whether any individual step is appropriate.

| Script | Statements and purpose | Historical assumptions |
| --- | --- | --- |
| [analytics/001_preflight.sql](../sql/analytics/001_preflight.sql) | SELECT checks over STAGING.TRA_TRAFFIC_CLEAN for counts, NULLs, duplicates and invalid values | Includes the original 2010–2026 year range. |
| [analytics/003_load_fact.sql](../sql/analytics/003_load_fact.sql) | Snowflake Scripting block with SELECT validation, insert-only MERGE, transaction handling and reconciliation | Exact 66 consecutive quarters from 2010 Q1 through 2026 Q2, fixed metric NULL counts and compatible existing target values. It supplies no REVISED-record UPDATE branch. |
| [analytics/005_reconcile.sql](../sql/analytics/005_reconcile.sql) | SELECT queries over fact and views for counts, NULLs, duplicates and coverage | Historical validation context, including a 2026 summary query. |

The load block requires the documented fresh worksheet transaction and paused
writers. Its safeguards and reconciliation conditions are preserved for review.
Future-quarter/revision publication needs a separately reviewed implementation.
The existing Python MERGE planner emits offline logical manifests with
`publication_allowed=false`. See the [planner guide](offline-merge-planning.md)
and [publication-readiness assessment](publication-readiness.md).

## Legacy RAW/OPS/MART scripts

The following five files support the historical Python loader or its earlier
schema proposals. They use different row identities, metadata columns, numeric
types or object names from the current contract. Preserve their path dependencies
until an explicit adapter/deprecation decision is reviewed.

| Script | Statements and purpose | Dependency or schema difference |
| --- | --- | --- |
| [001_bootstrap.sql](../sql/001_bootstrap.sql) | CREATE statements for database, RAW/STAGING/MART/OPS schemas, formats, stage and tables | Uses rendered `{{DATABASE}}`, metadata-enriched RAW.TRA_TRAFFIC and earlier NUMBER(38,6) models. Its CSV format expects a prepared one-header file, unlike the original bilingual export. |
| [002_merge_raw.sql](../sql/002_merge_raw.sql) | Insert-only MERGE into RAW.TRA_TRAFFIC using bound JSON | Keyed by source file/row hash and requires metadata columns absent from RAW.TRA_TRAFFIC_SOURCE. |
| [003_monitoring.sql](../sql/003_monitoring.sql) | SELECT monitoring queries | References the legacy RAW/OPS objects and rendered database placeholder. |
| [004_staging_preview.sql](../sql/004_staging_preview.sql) | SELECT-only typed preview of one source snapshot | Reads legacy RAW.TRA_TRAFFIC metadata, converts to NUMBER(38,6) and uses earlier long metric aliases. Requires a bound source hash. |
| [005_mart_preview.sql](../sql/005_mart_preview.sql) | SELECT-only quarter-date/metric projection | Reads legacy STAGING.TRA_TRAFFIC with a bound source hash. |

`telecom_pipeline/snowflake_loader.py` renders or executes these legacy templates
through its own approval option. The loader and tests depend on their existing
filenames and locations. Its presence supplies no current-contract incremental
publisher. Existing Snowflake objects and Python/template dependencies must be
reviewed together before considering relocation or reuse.

## Execution and evidence boundaries

- DDL files and the two MERGE files can modify warehouse state. Their presence
  in the repository provides source/review material, with separate execution approval.
- Preflight, reconciliation, monitoring and preview files contain SELECT queries.
  Legacy SELECTs still require their particular historical objects and parameters.
- The read-only Python adapter owns its fixed SELECT independently of these SQL
  files. Its live commands remain documented in the
  [read-only guide](snowflake-readonly-dry-run.md).
- Local tests, user-reported historical validation and unverified live SQL behavior
  retain their respective evidence scope in the
  [readiness assessment](publication-readiness.md).

Return to the [documentation index](README.md) for implemented features,
future design documents and archived verification reports.
