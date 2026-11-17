# TRA source inspection and manual RAW table setup

Follow-up: the user's completed manual Snowflake validation confirms the same
66 records and source-origin NULL counts. See [recorded findings and NULL policy](manual-data-quality-validation.md).
The loading steps below remain a historical/manual reference, not instructions
to reload already validated data.

Inspected locally on 2026-11-09 with Python 3.11 in `telecom_de`.
No Snowflake connection or SQL execution was performed for this inspection.
The user reports TELECOM_DB exists with RAW, STAGING, and ANALYTICS schemas;
this account state has not been independently verified by the Python connector.
No OPS/MART schema is required by the standalone manual setup below.

## Actual file structure

- Source: `data/raw/tra-open-data-traffic.csv`, 5,299 bytes.
- 66 data records, 9 columns, descending from 2026 Q2 to 2010 Q1.
- UTF-8, no BOM; Arabic header labels decode correctly.
- Comma delimiter, double-quoted years containing thousands separators.
- Lines 1–6: comment metadata; line 7: English column headers;
  line 8: Arabic header labels; line 9: empty comma-separated separator.
- Data starts on physical line 10. No metric units are reinterpreted.

## Columns and observed logical types

The existing loader deliberately returns nullable strings to preserve RAW values.
Logical types below were checked by Decimal parsing across all nonmissing cells;
these are appropriate future STAGING types, not inferred from a small sample.

| Exact column name | Observed logical type / suggested STAGING type | Missing | Minimum | Maximum |
| --- | --- | ---: | ---: | ---: |
| Year | Integer / NUMBER(4,0) | 0 | 2010 | 2026 |
| Quarter | Integer 1–4 / NUMBER(1,0) | 0 | 1 | 4 |
| Outgoing International Minutes Million | Decimal / NUMBER(38,2) | 0 | 89.60 | 300.93 |
| Incoming International Minutes Million | Decimal / NUMBER(38,2) | 0 | 28.24 | 134.04 |
| Mobile Local Minutes Million | Decimal / NUMBER(38,2) | 4 | 838.58 | 2377.10 |
| Fixed Local Minutes Million | Decimal / NUMBER(38,6) | 15 | 26.032916 | 157.024480 |
| Fixed Broadband Consumption Million Gb | Decimal / NUMBER(38,2) | 32 | 183.10 | 1413.60 |
| Mobile Broadband Consumption Million Gb | Decimal / NUMBER(38,2) | 32 | 30.50 | 194.97 |
| International Internet Capacity Mbps | Integer / NUMBER(38,0) | 1 | 7140 | 5382000 |

The two-decimal representation of Year/Quarter does not imply fractional periods:
every observed Year/Quarter is integral. Suggested metric scales describe this
snapshot; future source changes must be revalidated. Earlier planned warehouse
contracts use NUMBER(38,6) uniformly for metrics, which also accommodates this data.

## Data quality findings

- 84 missing metric cells across five columns; their cause is not documented.
  Missing means unavailable, not zero.
- No exact duplicate records or repeated year/quarter keys.
- No missing quarters within the observed 2010 Q1–2026 Q2 coverage.
- All populated numeric cells parse successfully, are finite and nonnegative;
  quarters pass the existing 1–4 business rule.
- Metadata says `Records: 68`, while the file contains 66 actual data records.
  The cause is unverified; do not fabricate two extra rows.
- Bilingual headers, metadata, and the blank separator are export structure,
  not data. A naive Pandas/warehouse import can treat them as records.
- Year strings such as `2,026.00` require safe thousands-separator handling.
- Six decimal places in fixed local minutes must not be rounded accidentally.
- Source provenance/download URL has not yet been independently established.

## Prepared SQL

Run `sql/006_create_raw_tra_traffic_source.sql` yourself after reviewing it.
It creates only `TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE` with the nine exact source
columns as nullable VARCHAR. All names are quoted so case and spaces are retained.
Text in RAW preserves thousands separators, trailing decimal zeros, and nulls;
numeric casting belongs in STAGING. No constraints force historical missing
metrics to zero or cause source values to be rejected during landing.

There is no CREATE OR REPLACE, DROP, or TRUNCATE. CREATE fails if the target table
already exists, rather than silently replacing/reusing its definition.
The script creates no database or schemas and never references CUSTOMERS.

This source-only manual landing table is separate from the previously proposed
RAW.TRA_TRAFFIC, which includes hashes, batch IDs, and timestamps. The existing
Python Snowflake loader and earlier SQL previews target that metadata-enriched
table, not TRA_TRAFFIC_SOURCE. Do not run the earlier bootstrap or loader for this
manual path; they also assume OPS/MART names that differ from your three schemas.
Any future automated adaptation to RAW/STAGING/ANALYTICS needs explicit review.

## Next steps for manual loading

1. Review and manually run the prepared CREATE TABLE script.
2. Prepare a separate upload CSV with one English header and 66 data records.
   Keep the original raw file unchanged. From the repository root, run this
   Python snippet in `telecom_de` when you are ready:

   ```python
   from pathlib import Path
   from telecom_pipeline.ingestion import load_tra_csv

   source = Path("data/raw/tra-open-data-traffic.csv")
   target = Path("data/processed/tra-traffic-upload.csv")
   target.parent.mkdir(parents=True, exist_ok=True)
   result = load_tra_csv(source)
   result.data.to_csv(target, index=False, encoding="utf-8", na_rep="", mode="x")
   ```

   Exclusive mode `x` refuses to overwrite an existing export. This snippet is
   provided for you and has not been executed as part of this request. It excludes
   metadata, Arabic headers, and the separator from the derived upload, while
   retaining them in the original file and reader inspection metadata.

3. Upload the derived file to TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE through your
   manual Snowflake loading workflow. Use UTF-8, comma delimiter, optional double
   quotes, one skipped header row, and empty fields mapped to SQL NULL. Map columns
   in the exact source order and keep VARCHAR types. A file format configured for
   one header is not suitable for the untouched nine-line TRA preamble/header area.
4. Load once into the new empty table, then check 66 rows, nine columns, zero
   duplicate period keys, and the missing counts above. For example, manually:

   ```sql
   SELECT COUNT(*) FROM TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE;
   SELECT "Year", "Quarter", COUNT(*) AS COPIES
   FROM TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE
   GROUP BY "Year", "Quarter" HAVING COUNT(*) > 1;
   ```

   Those queries have not been executed here. Confirm the first record retains
   Year `2,026.00` and fixed local minutes `27.862700`, and empty metric cells are
   SQL NULL rather than zero or the literal string `null`.
5. Record the loaded filename, SHA-256, and row count. This simple source-only
   table has no enforced deduplication or batch ledger. Do not append the same
   file again assuming this table will reject duplicates; establish explicit
   batch identity/MERGE handling before repeatable automated loading.
6. Plan validated numeric/date conversion into STAGING and later ANALYTICS after
   the RAW load is confirmed. No cloud transformations have been executed.

Original file fingerprint, unchanged after inspection:
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
