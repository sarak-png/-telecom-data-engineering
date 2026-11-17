# Offline FLOAT precision assessment policy

Policy version `OFFLINE_FLOAT_ASSESSMENT_V1` is implemented in
`telecom_pipeline/precision.py`. It profiles source formatting and assesses
individual metrics using canonical source values, Python binary64 projections,
and optional documented metric precision evidence. The API always emits
`publication_allowed=false`.

## Existing types and comparison behavior

Contract 1.1 records nine VARCHAR source columns in RAW. STAGING and ANALYTICS
use integer YEAR/QUARTER and FLOAT for all seven metrics. The legacy local
transformation module checks NUMBER(38,6) compatibility, which is an application
constraint of that earlier design. It supplies no authority for the precision
of the actual FLOAT warehouse or TRA measurements.

The incremental classifier hashes canonical Decimal values and compares all
seven metrics exactly. Formatting differences such as 100 and 100.00 compare
equal. The read-only adapter converts returned floats with Decimal(str(value))
and compares those approximations with incoming source Decimals. The offline
MERGE planner rejects those read-only approval contexts and requires canonical
accepted evidence. This new module is opt-in and has no callers in those paths.

Snowflake FLOAT uses approximate binary floating-point representation. Many
decimal fractions have no exact binary representation. Python's str(float)
provides a short decimal rendering that round-trips to the float, while
Decimal.from_float exposes the exact binary value. Different source decimals
can produce the same float, so a matching short rendering cannot establish the
original source precision. See [Snowflake numeric types](https://docs.snowflake.com/en/sql-reference/data-types-numeric),
[Python floating-point limitations](https://docs.python.org/3.11/tutorial/floatingpoint.html),
and [Decimal conversion documentation](https://docs.python.org/3.11/library/decimal.html).

## Observed source evidence

The original CSV contains 66 records spanning 2010 Q1 through 2026 Q2.
Its English/Arabic labels and declared row count provide no authoritative
precision, rounding or measurement-error specification. Profiling its literal
metric strings produced the following observations, including trailing zeros.

| Metric | Observed decimal places | Non-NULL values | NULL values | Authoritative source precision |
| --- | --- | --- | --- | --- |
| OUTGOING_INT_MIN_M | 2 | 66 | 0 | Pending evidence |
| INCOMING_INT_MIN_M | 2 | 66 | 0 | Pending evidence |
| MOBILE_LOCAL_MIN_M | 2 | 62 | 4 | Pending evidence |
| FIXED_LOCAL_MIN_M | 6 | 51 | 15 | Pending evidence |
| FIXED_BROADBAND_GB_M | 2 | 34 | 32 | Pending evidence |
| MOBILE_BROADBAND_GB_M | 2 | 34 | 32 | Pending evidence |
| INTERNET_CAPACITY_MBPS | 0 | 65 | 1 | Pending evidence |

These counts describe this file's formatting. They do not establish rounding
scales, acceptable error, or future-quarter precision. The profiler emits
`documented_decimal_places=null` and `precision_evidence=null` for every metric.
There are no built-in TRA tolerance or decimal-grid values.

## Deterministic comparison rules

1. Validate source values with the existing canonical numeric rules and require
   finite, nonnegative Python floats or None for observed warehouse values.
   Source floats are rejected because they have already lost source precision.
2. Preserve NULL and zero as distinct states. Two NULLs match. A NULL/value
   transition is a revision. Numerical positive/negative zero compare equally.
3. When canonical accepted source evidence is supplied, compare it with incoming
   Decimal exactly. That determines UNCHANGED or REVISED. Never round a source
   change away, including a change whose binary64 projection stays the same.
4. Assess warehouse consistency independently. Compare the returned float with
   the exact Python float projection of the accepted source value. Report MATCH,
   MISMATCH, NULL_MATCH, NULL_MISMATCH, OVERFLOW or UNDERFLOW. A canonical revision
   can coexist with a matching warehouse projection of the accepted old value.
5. Without accepted source evidence, numeric source-change status is UNRESOLVED
   unless an approved documented precision rule identifies a unique accepted
   grid value. Matching decimal rendering alone leaves the ambiguity unresolved.
   Incoming projection comparisons are explicitly labeled INCOMING_DIAGNOSTIC.
6. Report ULP distance from the expected projection using exact rational
   arithmetic. The distance is diagnostic and grants no acceptance tolerance.
   Even a one-ULP discrepancy is reported as MISMATCH. The previously proposed
   four-ULP threshold remains unapproved and is unused here.

`requires_review` is true for revisions, unresolved source comparisons, read-back
disagreements and range loss. It does not record a reviewer decision. The existing
classifier, registry approvals and source payload hashes retain their behavior.

## Metric-specific documented-grid proposal

`DocumentedPrecision` requires a metric alias, documented decimal places,
evidence reference and approval identity. Evidence should establish the metric's
units, exact decimal grid, relevant publication periods and source rounding or
truncation semantics. A project reviewer must verify that evidence independently.
The API validates the supplied references but cannot authenticate them or select
their effective period scope. Callers must apply a rule only within its reviewed
scope. Synthetic test specifications provide no TRA source authority.

Given an approved rule with scale s, candidate accepted values lie on the exact
grid k / 10^s. The assessor obtains the previous and next binary64 values and
constructs their exact rational midpoints around the returned float. It accepts
a recovered grid value only when exactly one nonnegative grid point lies strictly
inside that rounding cell and reprojects to the observed float. Any grid point
at a midpoint boundary, multiple candidates or zero candidates leaves the source
comparison UNRESOLVED. This conservative boundary rule avoids choosing among
ties and binary64 collisions. No additional epsilon or ULP allowance is used.

Incoming/accepted Decimal values must lie exactly on the documented grid.
Off-grid values raise validation errors instead of being quantized. Recovery
uses exact Fraction arithmetic and Decimal tuple construction, which preserve
results across Decimal context precisions. NULL transitions remain independent
of the grid. Very fine grids around float zero can remain ambiguous because
positive values may underflow to zero.

This proposal assumes direct correctly rounded Decimal-to-binary64 conversion.
Snowflake loading, casts, intermediate computations and connector read-back need
separate verification before adopting it for warehouse reconciliation. Integer
metrics also require assessment because adjacent sufficiently large integers
can share the same FLOAT.

## Usage and offline reproduction

From Anaconda Prompt in the project root, profile the original source formatting:

```bat
conda activate telecom_de
python -m telecom_pipeline.precision data/raw/tra-open-data-traffic.csv
```

The command reads the source and prints a deterministic JSON report. It creates
no output file. For individual offline assessments with known accepted evidence:

```python
from telecom_pipeline.precision import assess_metric

# Synthetic engineering example.
assessment = assess_metric(
    "OUTGOING_INT_MIN_M",
    incoming="0.100000000000000005",
    observed_float=0.1,
    accepted_source="0.1",
)
assert assessment["source_change"] == "REVISED"
assert assessment["readback"] == "MATCH"
```

Run the precision tests and existing comparison/planning regressions:

```bat
python -m pytest tests/test_precision.py tests/test_incremental.py tests/test_snowflake_readonly.py tests/test_merge_planning.py tests/test_warehouse_contract.py -q -p no:cacheprovider --basetemp data/ingestion/precision-tests-%RANDOM%-%RANDOM%
```

## Decisions requiring human approval

- Obtain authoritative TRA precision/grid and rounding evidence for each metric,
  including its units and effective publication periods. Observed two/six/zero
  decimal formatting is insufficient to approve those values as tolerances.
- Reconcile canonical accepted history with the real warehouse. A float-only
  historical snapshot cannot establish which original decimal was loaded.
- Review the unique-grid inference proposal and verify its conversion assumptions
  against the actual loading path. Decide whether any nonzero read-back tolerance
  is justified using measured evidence. Four ULPs remains a candidate only.
- Approve a separately scoped integration change before using this policy in
  the live read-only workflow, batch approval process or future publisher.

All current work is offline. The new assessor supplies no connector, SQL execution,
schema change, permission management or MERGE execution. Existing RAW data and
the synthetic CSV fixture serve as read-only inputs.

Verified on 2026-11-16 using `telecom_de`. The 39 new precision tests passed,
and the complete offline suite reported **341 passed in 4.17 seconds**, with
the existing live test explicitly excluded. The profiling command ran against
the original CSV. Fingerprints of all 44 pre-existing files under
`telecom_pipeline`, `tests`, `config`, `sql` and `data/raw` matched before and
after development, including the original CSV and synthetic fixture.
