# Manual Snowflake data quality validation

Recorded on 2026-11-09 from the user's completed manual Snowflake validation.
These are user-reported cloud results, consistent with the local source profile;
the agent did not connect to Snowflake or execute validation SQL.

| Check | Reported result |
| --- | --- |
| Valid records | 66 |
| Year range | 2010–2026 |
| Duplicate Year/Quarter combinations | 0 |
| Mobile Local Minutes Million NULLs | 4 |
| Fixed Local Minutes Million NULLs | 15 |
| Fixed Broadband Consumption Million Gb NULLs | 32 |
| Mobile Broadband Consumption Million Gb NULLs | 32 |
| International Internet Capacity Mbps NULLs | 1 |
| Total missing cells across these five metrics | 84 |

The user confirmed these missing values also exist in RAW source data. They
therefore do not, by themselves, indicate data loss introduced by loading or
transformation. Their business cause has not been established. No assumption is
made that the missing values mean zero usage, zero capacity, or service absence.
The other four source columns have no missing cells in the local CSV profile.
The full local source inspection is in [the source report](tra-source-inspection.md).

## NULL preservation policy

- Preserve source NULLs through RAW, STAGING, and ANALYTICS.
- Do not apply zero-filling, COALESCE(metric, 0), NVL(metric, 0), fillna(0),
  interpolation, or forward/backward filling to source metrics.
- Preserve genuine numeric zero as zero; it is different from NULL.
- Keep missingness separate from numeric values using coverage counts or flags.
- Invalid numeric text must fail validation or be explicitly reported; do not
  silently turn conversion failures into source NULLs.
- A metric stays nullable even if all of its present snapshot values are populated.
- SQL SUM/AVG ignore NULLs, so aggregate coverage must be explicit. A partial
  observed sum is not a complete annual total and must never be presented as one.
- No rounding, unit reinterpretation, or aggregation across incompatible measures.

The existing local transformation code already retains Decimal values and None
without zero-filling. This documentation introduces no table or code changes.
Local verification in `telecom_de`: two relevant source/transformation regression
tests passed in 0.69s. A separate comparison confirmed that every one of the
seven metrics retains its individual missing-value count through the local
staging and analytics-shaped output. The original raw SHA-256 still matches
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
Before the future Analytics load, compare NULL counts per metric, not only the
total of 84. Reconcile counts in the same validated snapshot to avoid confusing
source updates with transformation regressions.

## Acceptance baseline for the current snapshot

The proposed quarterly Analytics fact must contain 66 unique period keys and
retain the individual NULL counts above. The locally inspected period coverage
is 2010 Q1–2026 Q2; the exact staged period coverage must be checked against the
existing STAGING table before an Analytics implementation is finalized. An annual
2026 row must be marked partial, not treated as a full-year result.

No RAW/STAGING repair or backfill is proposed. Future legitimate source updates
may change this baseline; record their source identity and validate the new
snapshot rather than imposing these historical counts on every future batch.
