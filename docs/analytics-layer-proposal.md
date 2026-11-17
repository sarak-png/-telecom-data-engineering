# Analytics Layer proposal — awaiting review

## Confirmed-schema revision on 2026-11-10

The attached DESCRIBE export confirms STAGING.TRA_TRAFFIC_CLEAN with YEAR/QUARTER
as nullable NUMBER(38,0) and seven nullable FLOAT metrics. The current design and
prepared local SQL drafts are documented in [Analytics SQL review](analytics-sql-review.md).
That revision uses the exact abbreviated source aliases and retains FLOAT in
the proposed fact. Annual usage totals await source-semantic confirmation.
The older contract below records the original proposal before this schema was
available. Use the confirmed-schema revision for review and execution planning.

Target schema: **TELECOM_DB.ANALYTICS**, as created manually by the user.
This is a design proposal, not an implementation. No executable Analytics DDL or
DML has been added or run. Existing RAW and STAGING tables will remain untouched.
Earlier MART/OPS scripts are historical proposals and are not prerequisites.

The manual validation baseline is [documented here](manual-data-quality-validation.md).
The current STAGING table name, column definitions, and lineage fields still need
confirmation. Source-column mappings below are proposed aliases, not claims about
the actual existing STAGING schema. No new role, warehouse, or schema is required
by this proposal.

## Proposed objects

| Object | Type and grain | Purpose |
| --- | --- | --- |
| ANALYTICS.FACT_TELECOM_QUARTERLY | Table: one row per calendar year/quarter for a selected validated snapshot | Stable typed quarterly metrics for SQL consumers |
| ANALYTICS.VW_YEARLY_TELECOM_SUMMARY | View: one row per year derived from the quarterly fact | Annual totals with explicit completeness and year-end capacity |
| ANALYTICS.VW_TELECOM_DATA_COVERAGE | View: one row per year and metric | Missing/populated counts and coverage, separate from telecom measurements |

Only one physical fact table is proposed. Views avoid separately maintaining
duplicated annual/quality tables. A separate date dimension is unnecessary for
this single quarterly dataset. No customer, operator, region, or subscriber
dimensions are invented because they are absent from the source.

## Quarterly fact contract

| Proposed column | Type | Rule |
| --- | --- | --- |
| YEAR | NUMBER(4,0), NOT NULL | Validated calendar year |
| QUARTER | NUMBER(1,0), NOT NULL | 1–4 |
| QUARTER_START_DATE | DATE, NOT NULL | First day of the calendar quarter |
| OUTGOING_INTERNATIONAL_MINUTES_MILLION | NUMBER(38,6), nullable | Preserve staged value and NULL |
| INCOMING_INTERNATIONAL_MINUTES_MILLION | NUMBER(38,6), nullable | Preserve staged value and NULL |
| MOBILE_LOCAL_MINUTES_MILLION | NUMBER(38,6), nullable | Preserve staged value and NULL |
| FIXED_LOCAL_MINUTES_MILLION | NUMBER(38,6), nullable | Preserve staged value and NULL |
| FIXED_BROADBAND_CONSUMPTION_MILLION_GB | NUMBER(38,6), nullable | Preserve the source unit label and NULL |
| MOBILE_BROADBAND_CONSUMPTION_MILLION_GB | NUMBER(38,6), nullable | Preserve the source unit label and NULL |
| INTERNATIONAL_INTERNET_CAPACITY_MBPS | NUMBER(38,6), nullable | Capacity measurement; never sum across quarters |
| ANALYTICS_BATCH_ID | VARCHAR, NOT NULL | Identifier of a future approved execution |
| ANALYTICS_LOADED_AT | TIMESTAMP_TZ, NOT NULL | Actual future execution time in UTC |

Retain the existing source hash/batch lineage if available in STAGING. If it is
not present, agree a source-snapshot reference before implementation rather than
inventing original ingestion timestamps or modifying STAGING to add them.
The dataset is explicitly Oman's aggregate telecom traffic; no inferred geographic
breakdown is added. The six-decimal metric scale accommodates observed precision.

YEAR/QUARTER is the logical fact key. Duplicate periods must fail a preflight
check rather than be resolved using an arbitrary ROW_NUMBER selection. Exactly
one agreed source snapshot is selected; no automatic latest-snapshot policy will
be introduced. The initial expected row count is 66.

## Proposed SQL transformations (explanation only)

1. Read the confirmed STAGING table and explicit snapshot scope. Check period
   uniqueness, data types, numeric precision/ranges, and NULL counts first.
2. Project staged metrics directly to the fact columns. No filling, imputation,
   rescaling, inferred unit conversion, or totals combining different services.
3. Derive QUARTER_START_DATE with the expression
   `DATE_FROM_PARTS(YEAR, (QUARTER - 1) * 3 + 1, 1)`.
4. Add the actual Analytics execution batch/time and available source lineage.
5. Initially populate only an approved new Analytics table. The future SQL will
   fail on an existing incompatible object; it will not replace an existing
   object. A rerun may insert only absent period keys after reconciliation.
   Identical matches remain unchanged. Changed existing period values will stop
   for review rather than silently overwrite history. NULL-safe comparisons are
   required so NULL versus zero is treated as a real difference.
6. Verify one-to-one period coverage and all metric values using NULL-safe
   comparison, alongside the five individual NULL counts, before committing.
   Uniqueness must be checked explicitly, not assumed from a declared key.
7. Create the two views only after their definitions and target names are approved.

No SQL implementing these steps is being executed or staged for automatic use.
Cross-system/parallel writers are outside this initial serialized workflow.

## Annual view: safe aggregation

The first six metrics describe period usage/consumption. Proposed annual totals
are valid only if the source confirms that each value represents that quarter,
not a cumulative/year-to-date measure. Verify this interpretation before enabling
SUM-based annual totals; the file headers alone do not prove it.

For each such metric, expose the number of populated quarters. Publish its annual
total only when there are four distinct quarters and four non-NULL values.
Otherwise the annual total remains NULL. A stored zero contributes to completeness
and remains a genuine zero. Do not use SUM alone, because it can hide missing data.
In particular, 2026 has only Q1/Q2 locally and is a partial year.

International internet capacity is not additive over time. Proposed annual
output is Q4 capacity, sourced specifically from quarter 4; if Q4 is absent or
NULL, year-end capacity is NULL. Do not label the last available Q1/Q2 capacity
as year-end capacity, and do not sum Mbps values across quarters.

## Coverage view

Proposed columns: YEAR, METRIC_NAME, QUARTERS_PRESENT, OBSERVED_VALUE_COUNT,
NULL_VALUE_COUNT, EXPECTED_QUARTERS (4), ABSENT_QUARTER_COUNT, and a completeness
flag. Distinguish a missing period row from a NULL cell in an existing row. Emit
all seven metrics including an all-NULL metric; use a construction that retains
NULLs, such as UNPIVOT INCLUDE NULLS. All-NULL years must not disappear.

For partial years, EXPECTED_QUARTERS=4 describes full-year coverage, not a claim
that future quarters should already have arrived. Expose the observed latest
quarter so consumers can recognize the snapshot boundary. Count values may be
zero; the source measurement columns must never be zero-filled.

## Proposed tests and execution boundary

After design approval, implement tests for: 66 quarterly keys, unique periods,
quarter dates, exact metric equality/precision, NULL counts 4/15/32/32/1, retained
genuine zeros, idempotent reruns, conflicting updates, incomplete annual totals,
all-NULL coverage, and non-additive capacity. Use clearly labeled synthetic
fixtures for edge cases rather than modifying the TRA source.

Before cloud SQL implementation, confirm the actual STAGING table definition,
its complete snapshot scope/lineage, and whether quarterly usage metrics are
period totals. Before cloud execution, obtain explicit approval for each proposed
Analytics object and load. This document provides no approval to create/modify
objects, execute SQL, or change RAW/STAGING data.
