# TRA traffic dataset: acquisition and provenance

## Verified official source

Reviewed on 2026-11-17:

- Publisher: Oman's Telecommunications Regulatory Authority (TRA).
- Official traffic / usage indicators page:
  [TRA traffic open data](https://tra.gov.om/open-data/traffic?lang=ar).
- The page lists quarterly indicators from telecom operators and 66 results,
  with seven metrics and latest displayed records for 2026 Q2.
- The page explicitly links to this
  [Open Data Usage License (Arabic PDF)](https://tra.gov.om/files/uploads/rkhst_astkhdam_albyanat_almftwht_6bb1673e65.pdf).

The license permits commercial/noncommercial reuse, copying, distribution and
processing. Clause 3 requires clear original-source attribution and disclosure
of modifications/processing without misleading presentation. It excludes personal
data, government logos and protected third-party material; it grants no endorsement.
It states compatibility with CC BY 4.0; this guide does not replace the linked
license with a different license label. These are summaries; consult the original.

Suggested attribution for verified acquisitions: “Source: Telecommunications
Regulatory Authority, Sultanate of Oman, Traffic / Usage Indicators,” followed by
the source and license URLs above, the actual retrieval date and processing notes.
No specific credit wording is asserted to be mandated by TRA.

## Local historical snapshot: verified repository evidence

Expected local filename: `data/raw/tra-open-data-traffic.csv` (relative to the
repository root). This exact file is ignored by Git pending provenance resolution;
it is retained locally without modification.

| Property | Inspected local file |
| --- | --- |
| Size | 5,299 bytes |
| Encoding / delimiter | UTF-8 without BOM / comma |
| Layout | Six comment lines, English header on line 7, Arabic labels on line 8, empty separator on line 9 |
| Columns | Year, Quarter and seven metrics; exact names in the [source inspection](../../docs/tra-source-inspection.md) |
| Coverage | 66 consecutive quarterly records, 2010 Q1–2026 Q2 |
| Missing values | Seven metric NULL counts: 0, 0, 4, 15, 32, 32, 1 in original column order |
| Duplicate period keys | None |
| SHA-256 | `12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd` |

The metadata identifies TRA, reports `Records: 68` despite 66 parsed records,
and contains the literal timestamp `11/3/2026, 7:24:53 PM`. Its date convention
and the count discrepancy are unresolved. Neither establishes a download receipt.

The official page agrees on subject, latest quarters and representative values. For
2026 Q2, its displayed fixed-local value is `27.86`, while the local CSV contains
`27.862700`. A rounded display cannot establish exact export equivalence. No
original download URL, receipt or verified export-byte comparison is retained in
the repository. The official page's license therefore does **not yet establish
redistribution provenance for this specific file**. No claim is made about
unrecorded processing before the file entered the project.

The project has preserved the local raw bytes. Documented downstream processing
skips metadata/bilingual label rows, normalizes numeric formatting, validates keys
and metrics, and retains missing values as NULL. Snowflake's manually executed
workflow casts source strings into numeric/FLOAT fields and derives analytics
dates and lineage; these are derived representations, not edits to the raw CSV.

## Acquisition instructions and limitations

1. Open the official TRA page above. It exposes a “Download data” control
   (`تحميل البيانات`). Use the official download interface and inspect the offered
   formats; a direct CSV export URL and its exact bytes have not been verified here.
2. Save the original download unchanged. Record its exact download URL, retrieval
   date, selected filters, format, source attribution, license URL and SHA-256.
   Do not rename an Excel/JSON document to `.csv` or silently convert it.
3. If the download is a CSV matching the documented nine-column bilingual layout,
   place an unchanged local copy at the expected filename above. Keep download
   evidence privately until reviewed; do not commit account/session URLs or tokens.
4. Inspect it offline from the repository root:

   ```bat
   python -m telecom_pipeline.ingestion data/raw/tra-open-data-traffic.csv
   python -c "from pathlib import Path; import hashlib; print(hashlib.sha256(Path('data/raw/tra-open-data-traffic.csv').read_bytes()).hexdigest())"
   ```

5. A current official download may have a different layout, precision or coverage.
   Do not alter it to force a match. Retain it separately and request a reviewed
   compatibility/baseline update. To reproduce historical integrity tests, the
   exact snapshot above is needed; obtain it from the maintainer only after its
   acquisition and redistribution provenance are resolved.

The [root quickstart](../../README.md#safe-offline-quickstart), full offline suite
and original-file FLOAT checks depend on the local CSV. Several ingestion,
transformation, classifier, contract and mocked Snowflake tests read it; an
integrity test checks its exact historical hash. A fresh public clone cannot
reproduce the full recorded suite without this file. A newer download is not
promised to satisfy those assertions. The bundled
[synthetic fixture](../../tests/fixtures/synthetic_tra_incremental.csv) and its
[three offline tests](../../tests/test_incremental_fixture.py) need no TRA download:

```bat
python -m pytest tests/test_incremental_fixture.py -q -p no:cacheprovider --basetemp data/ingestion/fixture-tests-%RANDOM%-%RANDOM%
```

Synthetic metrics are engineering examples, not TRA measurements or publication
inputs. The narrow ignore rule does not exclude this synthetic fixture or this
guide. Revisit inclusion of the historical CSV only after acquisition provenance
and applicable redistribution conditions have been reviewed.

## Software licensing

No software `LICENSE` has been created. MIT is an option for the project owner's
decision; any future software license must keep third-party dataset terms separate.
