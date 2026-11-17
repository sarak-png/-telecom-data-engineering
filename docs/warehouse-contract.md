# Current warehouse contract

Recorded on 2026-11-15 for Step 1 of automation preparation. Contract version 1.1
incorporates the user's verified live DESCRIBE results for RAW and the fact table.
The canonical object, column, type, nullability, and source-to-target mapping is
`config/warehouse_contract.json`. Documentation and local checks reference that
file. Historical SQL definitions remain the evidence for their original designs.

## Confirmed object names

| Contract key | Fully qualified object | Kind | Column evidence |
| --- | --- | --- | --- |
| raw_source | TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE | Table | User-supplied verified live DESCRIBE results for nine nullable VARCHAR(16777216) columns |
| staging_clean | TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN | Table | Nine columns confirmed by the supplied DESCRIBE export |
| quarterly_fact | TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY | Table | User-supplied verified live DESCRIBE results for all thirteen columns |
| yearly_summary | TELECOM_DB.ANALYTICS.VW_YEARLY_TELECOM_SUMMARY | View | Six projected columns in sql/analytics/004_create_views.sql |
| data_coverage | TELECOM_DB.ANALYTICS.VW_TELECOM_DATA_COVERAGE | View | Nine projected columns in sql/analytics/004_create_views.sql |

The user confirmed all five object names. STAGING types/nullability are backed
by the supplied `tra_traffic_clean_schema_2026-11-10-0841.csv` DESCRIBE results.
RAW and fact types/nullability are now marked `verified_from_live_describe`,
based on the user's supplied results. The exact nine RAW names retain the
previously recorded spelling, case, and order. The user confirmed the two table
definitions have no primary key or unique key. Their `constraints` entries record
empty `primary_key` and `unique_keys` arrays. Missing constraint evidence for
other objects remains unverified.

STAGING retains its earlier DESCRIBE-export evidence for column names, types,
and nullability. This update supplies additional verification for RAW and the
fact table. View projection names are recorded from local SQL. View result types and nullability
use JSON null to indicate unknown metadata pending DESCRIBE results. That JSON
null describes missing contract evidence, separate from a NULL measurement.

The JSON stores column order as well as names. RAW names preserve the quoted
case and spaces from the source CSV. All nine RAW fields are nullable
VARCHAR(16777216). STAGING contains two nullable NUMBER(38,0) period keys and
seven nullable FLOAT measurements. The verified fact definition uses required
NUMBER(38,0) period keys and a required DATE quarter start. Its seven FLOAT
metrics are nullable. SOURCE_TABLE and ANALYTICS_BATCH_ID are required
VARCHAR(16777216), and ANALYTICS_LOADED_AT is required TIMESTAMP_TZ(9).
The source-to-STAGING-to-fact aliases
are explicitly enumerated in `column_mapping`.

RAW and STAGING have no declared file hashes or batch columns. The fact's
source-table reference identifies a relation, with source-snapshot lineage still
an open design issue. The current fact key remains YEAR/QUARTER. NULL metrics
retain their established meaning throughout the documented mapping.

## Safe configuration changes

`snowflake_config.py` reads the JSON using Python's standard library and exposes
`WAREHOUSE_CONTRACT` and `DEFAULT_DATABASE`. Importing this configuration reads
local metadata. Existing environment-variable requirements and explicit database
overrides keep their established behavior.

The Snowflake loader's default database now comes from that contract. Its offline
plan displays the five confirmed objects, contract version, and the legacy
executor's adaptation status. Confirmed object names in the plan always come
from the canonical contract. They describe the current project warehouse and
provide review context for future adapter work.

`.env.example` points to the canonical contract. Connection settings select a
session account/database/role, while adapting the historical executor requires
separate schema and behavior work. Its existing SQL, row representations, and
approval checks retain their original behavior in this Step 1 change.

The source validator's `COLUMNS` tuple remains the original CSV format contract.
The local transformation's long metric aliases and Decimal outputs retain their
existing export interface. Tests check their relationship to the warehouse
mapping and document the remaining representation differences.

## Legacy reference inventory and unresolved mismatches

| Reference | Locations | Why it needs adapter or migration work |
| --- | --- | --- |
| RAW.TRA_TRAFFIC | sql/001_bootstrap.sql, sql/002_merge_raw.sql, sql/003_monitoring.sql, sql/004_staging_preview.sql, snowflake_loader.py | Fourteen columns include RECORD_HASH, SOURCE_SHA256, SOURCE_ROW_NUMBER, FIRST_BATCH_ID, and LOADED_AT. Confirmed RAW.TRA_TRAFFIC_SOURCE contains nine source values. File-scoped MERGE and verification depend on those five metadata fields. |
| STAGING.TRA_TRAFFIC | sql/001_bootstrap.sql, sql/005_mart_preview.sql | Long metric aliases, NUMBER(38,6), and hash/batch/time fields differ from the supplied nine-column FLOAT-based TRA_TRAFFIC_CLEAN schema. |
| MART.FACT_TELECOM_QUARTERLY | sql/001_bootstrap.sql | Long metric aliases, fixed-point types, and different lineage fields prevent a schema-only substitution to the ANALYTICS fact. |
| OPS.INGESTION_BATCHES | sql/001_bootstrap.sql, sql/003_monitoring.sql, snowflake_loader.py | Audit inserts/updates require a separate table absent from the five confirmed objects. Batch-audit placement needs a later design decision. |
| Legacy RAW/OPS executor tests | tests/test_snowflake.py, tests/test_snowflake_live.py | Mock and live tests exercise the historical adapter. The live test targets the metadata-enriched RAW path through that adapter. |
| Local transformation representation | telecom_pipeline/transformation.py, tests/test_transformation.py | Output uses long aliases, Decimal metrics, source hashes, and a `mart` export key. It needs a reviewed adapter to the actual short-name FLOAT schema. |
| Historical documentation | docs/phase3-snowflake.md, docs/phase3-verification.md, docs/analytics-layer-proposal.md, docs/tra-source-inspection.md | Retains the earlier architectural proposals and validation chronology. Use this contract for current object identification. |

The explanatory legacy mention in sql/006_create_raw_tra_traffic_source.sql
remains historical context. The synthetic TELECOM_DE database identifier in
tests/test_snowflake.py exercises legacy rendering and connection-independent
behavior. The TELECOM_DE_PORTFOLIO application string is telemetry metadata.
The Conda environment name `telecom_de` continues to identify the local runtime.

Each incompatible legacy object has `automatic_replacement` set to JSON null
and a reason in the contract. Each entry records the adapter work required.
Plain text substitution cannot supply the missing lineage fields,
reconcile changed numeric representations, or choose an audit-storage design.

## Historical baseline

`historical_baseline` records the original 66 rows, 2010 Q1 through 2026 Q2,
source SHA-256, and the seven metric NULL counts 0, 0, 4, 15, 32, 32, and 1.
Its scope is the validated historical fixture. The existing Analytics load block
continues to enforce that snapshot through its original SQL logic.

All historical SQL files, including the Analytics load block, are preserved
byte-for-byte in this change. The original raw file is preserved as well.
The contract is a local metadata layer for subsequent reviewed implementation.
Incremental processing and scheduling remain future work.

## Local verification

`tests/test_warehouse_contract.py` checks the exact five-object set, the complete
nine-column mapping, STAGING DESCRIBE metadata, verified RAW/fact metadata,
view projection names, evidence gaps, and the historical source fingerprint and
NULL counts. It also exercises the default offline plan with cloud execution
replaced by a failing sentinel. SQL files are inspected as text.

The historical CREATE scripts use VARCHAR and TIMESTAMP_TZ without explicit
parameters. Their text checks normalize those declarations to the explicit
VARCHAR(16777216) and TIMESTAMP_TZ(9) values supplied by the verified DESCRIBE
results. Contract values retain the full precision and length information.

For this request, run the contract and other connection-independent tests with
pytest caching disabled and a fresh project-local temporary directory. Exclude
`tests/test_pipeline.py`, which executes SQLite SQL, and the opt-in Snowflake live
test to respect the requested execution scope. The legacy Snowflake unit tests
use in-memory fakes to examine SQL strings.

```powershell
conda activate telecom_de
python -m pytest tests/test_warehouse_contract.py tests/test_ingestion.py tests/test_transformation.py tests/test_auth_check.py tests/test_snowflake.py -q -p no:cacheprovider --basetemp data/ingestion/contract-tests-NEW-UNUSED-DIRECTORY
```

Choose a fresh temporary path for each run because pytest may clean an existing
basetemp directory.

### Contract 1.1 verification on 2026-11-15

The local contract suite passed **17 tests in 0.67s** using `telecom_de`:

```powershell
python -m pytest tests/test_warehouse_contract.py -q -p no:cacheprovider
```

The update modifies config/warehouse_contract.json, this guide, and
tests/test_warehouse_contract.py. Verification compared the original RAW names,
STAGING/view metadata, alias mapping, historical baseline, SQL files, Python
application files, and original CSV against their pre-update values. Every
comparison matched. Tests inspected local metadata and SQL text.

### Initial contract 1.0 verification on 2026-11-15

The selected suite passed **64 tests in 0.83s** using the dedicated `telecom_de`
interpreter. Fourteen cases cover the new warehouse contract and fifty cover
the existing source, transformation, authentication, and legacy fake-connection
behavior. Two initial view-inspection checks exposed limitations in the new test
parser for digit-containing aliases and direct projection columns. Those checks
were corrected before the successful run.

The SQLite-executing pipeline tests and live Snowflake test were excluded from
this run. SQL text checks and fake connections supplied the contract assertions.
Fingerprint comparison confirmed all twelve existing SQL files and the original
raw CSV were byte-for-byte identical to their pre-change versions.

New files are config/warehouse_contract.json, this guide, and
tests/test_warehouse_contract.py. Modified files are README.md, .env.example,
telecom_pipeline/snowflake_config.py, telecom_pipeline/snowflake_loader.py, and
the descriptive module header in tests/test_snowflake.py. Pytest temporary files
and Python bytecode are generated under ignored runtime directories.
