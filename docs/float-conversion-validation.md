# Manual read-only FLOAT conversion validation

`telecom_pipeline.float_validation` compares original CSV Decimal values with the
existing Analytics FLOAT values through the fixed-SELECT read-only adapter.
It reuses `precision.assess_metric()` with its existing policy and supplies no
documented-grid rule or numerical tolerance. Every success and error report
includes `publication_allowed=false`.

## Exact manual command

Use Anaconda Prompt with your existing OAuth account and username environment
variables already configured privately. Open the prompt at the repository root, then run:

```bat
conda activate telecom_de
set "SNOWFLAKE_ROLE=TELECOM_READONLY"
set "SNOWFLAKE_AUTHENTICATOR=OAUTH_AUTHORIZATION_CODE"
set "SNOWFLAKE_WAREHOUSE=SNOWFLAKE_LEARNING_WH"
set "SNOWFLAKE_DATABASE=TELECOM_DB"
python -m telecom_pipeline.float_validation data/raw/tra-open-data-traffic.csv --live-read-only --diagnostics
```

The account/user settings are reused from your terminal. The existing OAuth
configuration uses PKCE and disables credential caching. Connection details and
raw connector exception messages are excluded from report output. The role check
rejects any configured role other than TELECOM_READONLY before connecting.
`--live-read-only` explicitly authorizes the manual connection. Help and calls
without this flag perform no live read.

The adapter issues its one existing SELECT of YEAR, QUARTER and seven metrics
from `TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY`. It closes the connection and
cursor afterward. The command has no write path, registry persistence, approval
recording, SQL override or tolerance argument. Reading a suspended warehouse can
resume it and incur normal SELECT compute costs.

## What the report checks

The shared reader parses and hashes one byte snapshot of the CSV. The report
joins periods on YEAR/QUARTER and assesses all seven metrics for every source
period. For the original snapshot this is 66 periods and 462 metric checks.
It records whether the source SHA-256 matches the contract's historical fixture.
Duplicate source/warehouse keys and malformed values fail validation. Missing
warehouse periods are reported explicitly. Extra warehouse periods are listed
separately so future quarters do not invalidate historical comparisons.

For each metric, the report includes result counts, source NULL/zero counts,
and deterministic representative samples. Samples cover the first occurrence
of each result category, the first zero if present, and source minimum/maximum
values. All missing-period and numeric/NULL discrepancies also appear in
`differences_requiring_review`.

Sample details include the original literal decimal text, returned FLOAT's short
decimal rendering, its exact binary value expanded as Decimal, equality with the
short rendering, and exact ULP distance from Python's expected projection.
The adapter transports native floats through Decimal(str(float)). This command
converts those round-trip representations back to Python floats for assessment,
which preserves the returned binary64 value. It does not recover original source
precision from the warehouse. See [Python float representation](https://docs.python.org/3.11/tutorial/floatingpoint.html)
and [Decimal conversion](https://docs.python.org/3.11/library/decimal.html).

| Result | Meaning |
| --- | --- |
| EXACT_NUMERIC_MATCH | The source Decimal is exactly representable and equals the observed binary64 value |
| EXPECTED_BINARY_REPRESENTATION | The observed value equals float(source Decimal), while its exact binary expansion differs from that Decimal |
| NULL_MATCH | Both source and warehouse are NULL |
| NULL_MISMATCH | One side is NULL and the other has a value, including zero |
| UNEXPLAINED_FLOAT_MISMATCH | The returned float differs from the expected Python projection, including a one-ULP difference |
| OVERFLOW / UNDERFLOW | The source projection loses representable range, including a positive source value becoming zero |
| MISSING_PERIOD | The source YEAR/QUARTER key is absent from the returned warehouse snapshot |

EXPECTED_BINARY_REPRESENTATION identifies an apparent numeric difference that
agrees with binary64 representation. For example, an input decimal 0.1 and the
returned float 0.1 have different exact mathematical values despite identical
short text. This agreement cannot prove the original loaded source, provenance,
or specific historical conversion path because multiple decimals may map to the
same FLOAT. UNEXPLAINED_FLOAT_MISMATCH can reflect a later correction, another
load transformation or conversion behavior. Keep that result for review.

NULL and zero stay distinct throughout the assessment. No values are filled,
rounded to observed source formatting or accepted using an epsilon/ULP allowance.
The existing precision policy and live incremental comparison retain their APIs
and behavior. See the [precision policy](float-precision-policy.md) for its
authoritative precision-evidence requirements.

## Results and next review

Exit code 0 means `PROJECTION_CHECKS_PASSED`. This reports agreement with the
Python projection for source values and matching NULL states. Exit code 2 means
`REQUIRES_REVIEW` because discrepancies, range loss or missing periods were found.
Exit code 1 reports a sanitized configuration, connection, input or runtime error.
`--diagnostics` includes the existing safe stage/category diagnostics when available.

Review any discrepancies against private source/audit evidence and the warehouse
history. Tolerance approval, policy changes and publication remain separate
decisions. The original CSV stays a comparison reference even if later warehouse
corrections have changed a historical quarter.

## Offline verification

Run from the project root in the dedicated environment:

```bat
conda activate telecom_de
python -m pytest tests/test_float_validation.py tests/test_precision.py tests/test_snowflake_readonly.py tests/test_readonly_diagnostics.py -q -p no:cacheprovider --basetemp data/ingestion/float-validation-tests-%RANDOM%-%RANDOM%
```

Tests use mocked connections that accept only the adapter's fixed SELECT.
They cover the original CSV against a mocked binary64 projection, representation
differences, NULL/zero transitions, a one-ULP discrepancy, underflow, missing
periods, role enforcement, opt-in requirements, credential redaction and repeatable
reports for the same snapshot. The mocked original-file comparison provides no
live Snowflake conversion result. The manual command above remains for the user
to execute.

Verified locally on 2026-11-16 in `telecom_de`. All 20 new validation tests
passed. The complete offline suite, explicitly excluding the live test, reported
**361 passed in 4.43 seconds**. CLI help was exercised. Fingerprints of all 46
pre-existing files under `telecom_pipeline`, `tests`, `config`, `sql` and `data/raw`
matched before and after development, including the precision module, comparison
adapter, original CSV and synthetic fixture. No live query was run during this task.
