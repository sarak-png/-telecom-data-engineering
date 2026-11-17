# Phase 2 verification — 2026-11-08

Executed using the Python interpreter in the dedicated `telecom_de` Conda environment.
Python 3.11.18, Pandas 3.1.1, pytest 9.2.0; openpyxl 3.1.5 was added after approval.
`python -m pip check` returned `No broken requirements found.`

## Actual-source integration results

Command executed twice:

```powershell
python -m telecom_pipeline.pipeline data/raw/tra-open-data-traffic.csv
```

| Batch ID | Status | Rows read | Inserted | Already present |
| --- | --- | --- | --- | --- |
| d1d5ea20-9ca6-4004-91ec-a3876f462883 | SUCCEEDED | 66 | 66 | 0 |
| 80897dc0-25c8-4778-b621-42b3439905d3 | SUCCEEDED | 66 | 0 | 66 |

Direct SQLite inspection confirmed 66 stored records and both successful batch
reports. The runtime ledger is `data/ingestion/ingestion.sqlite3` and is ignored
by Git. Both runs read nine columns and detected zero duplicate input records.

| Source column | Missing cells |
| --- | --- |
| Year | 0 |
| Quarter | 0 |
| Outgoing International Minutes Million | 0 |
| Incoming International Minutes Million | 0 |
| Mobile Local Minutes Million | 4 |
| Fixed Local Minutes Million | 15 |
| Fixed Broadband Consumption Million Gb | 32 |
| Mobile Broadband Consumption Million Gb | 32 |
| International Internet Capacity Mbps | 1 |
| **Total** | **84** |

Source metadata says 68 records, while the parser finds 66. Each successful batch
includes that warning. Historical metric gaps remain null. UTF-8 decoding,
Arabic headers, quoted years, and the bilingual export layout continue to pass
the Phase 1 regression tests.

## Final test run

```text
python -m pytest -q
.............................                                            [100%]
29 passed in 1.51s
```

This includes the original seven tests and 22 Phase 2 cases: real-source reruns,
JSON event logs, CSV/Excel equivalence, named worksheets, formula rejection,
schema and numeric validation, null policy, duplicate and conflicting periods,
changed observations, transactional rollback/recovery, failure history, explicit
encoding, source-directory protection, numeric normalization, and concurrent
reruns. Synthetic values appear only in temporary engineering test fixtures.

## Raw-file integrity and scope

The source SHA-256 before and after work matches the original Phase 1 fingerprint:

```text
12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd
```

No original raw dataset was modified. No Snowflake connection or Phase 3 work
was performed. The operational limits and execution commands are documented in
[Phase 2 ingestion](../phase2-ingestion.md), including retained observation history,
SQLite state retention, and interrupted RUNNING batches.
