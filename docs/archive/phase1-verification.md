# Phase 1 verification — 2026-11-08

**Current environment:** the dedicated `telecom_de` environment now replaces the
earlier verification environments below. All seven tests passed with Python
3.11.18, Pandas 3.1.1, and pytest 9.2.0, using only packages installed in
`telecom_de`. See [current setup and results](../telecom-de-environment.md).
The remaining environment information records the earlier verification run.

Anaconda's `%USERPROFILE%\.conda\environments.txt` lists both installations:

| Environment | Python | Pandas | pytest | Jupyter | openpyxl |
| --- | --- | --- | --- | --- | --- |
| `<CONDA_ROOT>` | 3.14.6 | 3.0.3 | 9.0.3 | Available | Available |
| `<CONDA_ROOT>\envs\omantel` | 3.11.18 | 3.1.0 | Absent | Available | Absent |

Python 3.11 `omantel` matches the project target. Excel support is outside Phase 1.
No software was installed. The requirements upper bounds were widened to include
the Pandas and pytest versions actually exercised here; every allowed version
has not been tested.

Paths above and below use neutral placeholders: `%USERPROFILE%` is the user
profile directory and `<CONDA_ROOT>` is the Anaconda installation directory.
These commands record the historical verification procedure; replace placeholders
with local paths before use. Current setup is linked above.

## Reproduce with existing software

From the project root in PowerShell:

```powershell
& '<CONDA_ROOT>/envs/omantel/python.exe' -m telecom_pipeline.ingestion data/raw/tra-open-data-traffic.csv
& '<CONDA_ROOT>/python.exe' -m pytest -q
```

For the Python 3.11 test run, borrow existing pytest from base without installing
it. Append the base path after `omantel` paths so its Pandas takes precedence:

```powershell
& '<CONDA_ROOT>/envs/omantel/python.exe' -c "import sys,pandas; sys.path.append(r'<CONDA_ROOT>\Lib\site-packages'); import pytest; print(sys.version, pandas.__version__, pytest.__version__); raise SystemExit(pytest.main(['-q','-p','no:cacheprovider']))"
```

## Actual source results

- Shape: **66 rows × 9 columns**, all nullable Pandas strings.
- Strict UTF-8 decoding succeeds; no byte order mark is present.
- Six metadata lines; English header line 7, Arabic header line 8, one empty
  separator row on line 9. Comma delimiter and quoted year values parse correctly.
- Arabic labels include `السنة` and `الربع`; the loader retains all nine Arabic
  labels in its inspection result. UTF-8 round-trip succeeded for every label.
- Declared count 68 differs from actual 66; reported without altering records.
- Duplicate full records: 0. Independent PowerShell check found 66 unique period keys.

Exact column names are listed in README.md. Selected actual values:

| Year | Quarter | Outgoing International Minutes Million | Incoming International Minutes Million | Mobile Local Minutes Million |
| --- | --- | --- | --- | --- |
| 2,026.00 | 2.00 | 120.52 | 28.24 | 2377.10 |
| 2,026.00 | 1.00 | 123.61 | 28.83 | 2171.75 |
| 2,010.00 | 1.00 | 131.95 | 134.04 | missing |

First record's remaining values, in source column order: Fixed Local Minutes
Million `27.862700`; Fixed Broadband Consumption Million Gb `1413.60`; Mobile
Broadband Consumption Million Gb `194.97`; International Internet Capacity Mbps
`5382000`. Strings preserve decimal precision and formatting.

Missing cells by column: Year 0; Quarter 0; outgoing international 0; incoming
international 0; mobile local 4; fixed local 15; fixed broadband 32; mobile
broadband 32; international internet capacity 1.

## Tests and source protection

All seven pytest cases passed under each interpreter. The source regression test
checks shape, metadata, English/Arabic header handling, quoted year values,
missing cells, duplicates, and byte-for-byte immutability. Other cases cover BOM,
repeatability, malformed record widths, empty data, invalid encoding, duplicate
preservation, and literal `NA` preservation.

Raw source SHA-256 before and after execution:
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.

Changes in this continuation: strengthened `tests/test_ingestion.py`, widened
`requirements.txt` bounds to include verified local versions, updated README.md,
and added this report. Loader code needed no correction. Test execution also
creates disposable Python bytecode and pytest cache files, covered by .gitignore.
Phase 2 has not started.
