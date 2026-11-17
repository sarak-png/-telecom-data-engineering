# Analytics SQL review draft

Prepared on 2026-11-10 from the supplied DESCRIBE export
`tra_traffic_clean_schema_2026-11-10-0841.csv`.

Source table is `TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN`. The export confirms nine
nullable columns. It supplies schema metadata. The 66-row data baseline comes
from the user's separate manual validation and the local CSV inspection.

## Confirmed schema and proposed fact mapping

| Exact source and fact column | Confirmed source type | Proposed fact type | Current NULL baseline |
| --- | --- | --- | ---: |
| YEAR | NUMBER(38,0), nullable | NUMBER(38,0), required | 0 |
| QUARTER | NUMBER(38,0), nullable | NUMBER(38,0), required | 0 |
| OUTGOING_INT_MIN_M | FLOAT, nullable | FLOAT, nullable | 0 |
| INCOMING_INT_MIN_M | FLOAT, nullable | FLOAT, nullable | 0 |
| MOBILE_LOCAL_MIN_M | FLOAT, nullable | FLOAT, nullable | 4 |
| FIXED_LOCAL_MIN_M | FLOAT, nullable | FLOAT, nullable | 15 |
| FIXED_BROADBAND_GB_M | FLOAT, nullable | FLOAT, nullable | 32 |
| MOBILE_BROADBAND_GB_M | FLOAT, nullable | FLOAT, nullable | 32 |
| INTERNET_CAPACITY_MBPS | FLOAT, nullable | FLOAT, nullable | 1 |

Period keys become required in the proposed fact after the load validates their
presence and range. Measurement columns stay nullable and are copied directly.
The seven FLOAT types reflect the actual STAGING definition. FLOAT has approximate
numeric representation. Converting existing FLOAT values to NUMBER would require
an explicit rounding/precision policy and would not recover original decimal
text. The proposed fact therefore retains FLOAT. For derived floating-point
calculations, assess tolerances separately from copying stored source values.
[Snowflake numeric type documentation](https://docs.snowflake.com/en/sql-reference/data-types-numeric)

## Proposed objects and transformations

| Object | Grain | Contents |
| --- | --- | --- |
| ANALYTICS.FACT_TELECOM_QUARTERLY | One row per year/quarter | Exact source metric aliases, quarter date, source table reference, actual Analytics batch/time |
| ANALYTICS.VW_YEARLY_TELECOM_SUMMARY | One row per year | Quarter coverage, full-year flag, Q4 internet capacity |
| ANALYTICS.VW_TELECOM_DATA_COVERAGE | One row per year/metric | Populated and NULL counts, absent quarters, latest quarter, completeness |

QUARTER_START_DATE uses `DATE_FROM_PARTS(YEAR, (QUARTER - 1) * 3 + 1, 1)`.
SOURCE_TABLE records the known source relation. ANALYTICS_BATCH_ID and
ANALYTICS_LOADED_AT are generated during an approved load. The source schema
contains no batch/hash columns, so precise source-file lineage is still limited
to the separately documented manual source validation.

The yearly summary currently focuses on coverage and Q4 capacity. Usage SUM
columns await confirmation that the six usage/consumption measures are individual
quarter totals. Once confirmed, each annual total would require four populated
quarters or return NULL. Internet capacity represents a capacity measurement and
is never summed across quarters. Q4 capacity stays NULL when Q4 is absent or NULL.
For the present snapshot, 2026 has Q1/Q2 and a partial-year flag.

Coverage uses UNPIVOT INCLUDE NULLS to retain all-NULL metric/year combinations.
Its count columns distinguish absent period rows from NULL cells. Count values
may legitimately be zero. All measurement values retain their original NULL
state and genuine numeric zeros. [Snowflake UNPIVOT documentation](https://docs.snowflake.com/en/sql-reference/constructs/unpivot)

## Files prepared for approval

1. `sql/analytics/001_preflight.sql` reads counts, NULLs, duplicate keys, invalid
   periods, and negative/non-finite metric values from the confirmed source.
2. `002_create_fact.sql` defines the fact with the types above. Plain CREATE
   reports an existing target object for review.
3. `003_load_fact.sql` contains a transactional insert-only MERGE with baseline
   validation, existing-target conflict checks, and read-back reconciliation.
4. `004_create_views.sql` defines the two views using plain CREATE.
5. `005_reconcile.sql` provides read-only counts and coverage queries.

The scripts are review drafts. Their statements have not been submitted to
Snowflake. Approval is required before manual execution. Any live SQL syntax,
privileges, transaction behavior, and row-count results still need verification.

## Proposed load behavior

The current table is treated as a single validated snapshot. The source lacks
a snapshot selector, so the draft scopes all 66 rows and requires source/target
writers to be paused for the entire execution. Run the block as a complete unit
in a worksheet with a fresh transaction context. One writer controls the target.

The block validates 66 distinct quarters from 2010 Q1 to 2026 Q2, required period
keys, finite nonnegative populated measurements, and all seven NULL counts.
It rejects duplicate target keys and target periods/values that conflict with
the current source. It then inserts absent periods, derives quarter dates, and
records actual execution metadata. Matching rows retain their first load metadata.
An identical rerun should insert zero rows. Updated source measurements require
review because this draft raises a conflict instead of changing matched rows.

The comparison uses NULL-safe IS DISTINCT FROM for direct stored-value copies.
It performs no arithmetic on copied measurements. NULL versus zero is a conflict.
Every source key/value is reconciled and the target count must be 66 before commit.
Failures inside the transaction roll back its inserts. These guarantees assume
the documented single-writer and source-stability conditions.
[Snowflake NULL-safe comparison](https://docs.snowflake.com/en/sql-reference/functions/is-distinct-from)

The hard-coded counts and period interval deliberately describe this approved
historical snapshot. Future source updates require a reviewed baseline change.
DDL and load statements are separate files because DDL has different transaction
semantics. Review and approve each object and load before running the sequence.

The earlier `sql/004_staging_preview.sql` and `sql/005_mart_preview.sql` describe
the old metadata-enriched design. The new `sql/analytics/` drafts use the confirmed
TRA_TRAFFIC_CLEAN column names and ANALYTICS target schema.

## Local verification on 2026-11-10

The nine fact source-column declarations matched the names and types in the
attached DESCRIBE export. Static checks verified Analytics write targets,
insert-only MERGE, seven NULL-safe value comparisons, and NULL-inclusive coverage.
These checks inspected SQL text.

The existing local regression suite passed 72 tests in 2.49s with the live test
excluded. Initial runs encountered Windows access errors in pytest temporary
directories. A fresh directory under the ignored data/ingestion path resolved
the errors. The successful command disabled pytest caching and supplied that
fresh directory through --basetemp. Application code required no changes.

The original CSV fingerprint remained
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
Snowflake compilation, permissions, and execution results await approved live
verification. Approval for the proposed schema/types/load and any annual usage
aggregation remains with the user.
