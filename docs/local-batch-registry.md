# Offline SQLite batch registry

`telecom_pipeline.batch_control.BatchRegistry` implements the local control-state
portion of the incremental design using contract 1.1. It calls the existing
offline classifier. Existing ingestion commands and production loading behavior
are unchanged. No warehouse DDL, DML, connector calls, or scheduling are involved.
Python's built-in `sqlite3` is the only persistence dependency added by this module.

## Storage and identity

Use a **local, non-synchronized disk**, for example a path under
`$env:LOCALAPPDATA\telecom_de\control`. Avoid OneDrive, network drives, and shared
database files. Tests use disposable SQLite files under ignored
`data/ingestion/` directories. Do not commit a working registry or its journals.

| Local table | Responsibility |
| --- | --- |
| `batches` | Stable UUIDv5 batch identity, source SHA-256, intent/input hashes, input mode, schema/canonicalization/policy versions, immutable classification plan, accepted-state fingerprint, status, checkpoint, UTC timestamps, current attempt and coded error |
| `attempts` | UUIDv4 attempt identity, start/end timestamps, RUNNING/FAILED/INTERRUPTED/COMPLETED outcome |
| `revisions` | Deterministic revision identity bound to batch, YEAR/QUARTER, before/after hashes, complete canonical payloads, changed metrics, creation timestamp |
| `decisions` | Append-only, versioned APPROVE/REJECT decisions, exact before/after hashes, reviewer, non-secret rationale and decision timestamp |
| `events` | Ordered durable status/checkpoint history with attempt identity, UTC time and event code |

The intent hashes deterministic sorted-key compact JSON containing dataset ID,
lowercase source file SHA-256, input mode, declared scope, contract version,
canonicalization version and policy version. Batch UUIDv5 uses Python's
`NAMESPACE_URL` and the name `telecom-batch:` followed by that intent hash.
Same bytes under another filename produce the same identity. Different scope,
mode or policy is a different intent. Ordinals and canonical input hashes detect
inconsistent parsed rows supplied under the same file identity.

The caller must calculate SHA-256 from the **same immutable bytes** it parses;
the registry accepts a digest and records, and cannot independently prove this
binding. Invalid row shapes/types raise before registration; rejected scope or
conflicting-duplicate plans are durably registered FAILED. Archiving malformed
files and recording pre-classification failures remain ingestion-adapter work.

Decimal values persist as JSON text and missing values as JSON null. No FLOAT
tolerance is introduced. Canonical payload hashes determine equality, preserving
precision, NULL-to-value changes and value-to-NULL changes. Original CSV files
are never written by this module.

## Lifecycle and approval

Registration classifies the complete input and records checkpoint `CLASSIFIED`:

- NEW/UNCHANGED-only plans become `PENDING`.
- Any revision holds the entire batch in `WAITING_APPROVAL`.
- Scope failures or conflicting duplicates become `FAILED` and cannot start.

`decide()` requires a known revision ID, explicit decision, reviewer, rationale,
and matching expected-before/proposed-after hashes. One rejected revision holds
the entire batch FAILED with `REJECTED_REVISION`. Re-registering or retrying does
not erase that decision. A reviewer may explicitly supersede a decision before
an attempt starts; all versions remain available through `decisions()`.
All revisions must be approved to return the batch to PENDING. Decisions cannot
override classifier rejection, and are frozen during RUNNING and after COMPLETED.

`start()` atomically claims a fresh attempt. `prepare_publication()` checks the
attempt, approvals, and the supplied current accepted-state fingerprint before
returning only NEW and approved REVISED canonical records. It records checkpoint
`PUBLISH_READY`. UNCHANGED records produce no proposed writes.
`complete_offline()` repeats these checks and records COMPLETED with checkpoint
`OFFLINE_COMPLETED`. **This means local simulation completed; it does not mean
data was written to Snowflake or even to local accepted-data tables.**

COMPLETED is terminal: registering the same input returns the existing batch,
and `start()` returns `None`. Late failure/recovery attempts cannot downgrade it.

## API example

Run from the project root with `conda activate telecom_de`. This synthetic
example does not read or modify the TRA source or publish data:

```python
import hashlib
import os
from pathlib import Path
from telecom_pipeline.batch_control import BatchRegistry
from telecom_pipeline.incremental import METRIC_COLUMNS

# Synthetic engineering fixture, not an actual TRA observation.
row = {"YEAR": 2026, "QUARTER": 3, **dict.fromkeys(METRIC_COLUMNS, "100")}
source_bytes = b"synthetic-example-v1"
registry_path = Path(os.environ["LOCALAPPDATA"]) / "telecom_de/control/batches.sqlite3"
accepted = []  # Real integration must supply authoritative canonical observations.

with BatchRegistry(registry_path) as registry:
    batch_id = registry.register(
        [row], accepted,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        input_mode="QUARTER_DELTA",
        declared_periods=[(2026, 3)],
    )
    attempt = registry.start(batch_id)
    if attempt is not None:
        proposed = registry.prepare_publication(
            batch_id, attempt, current_accepted=accepted,
        )
        # Inspect/test the proposal here. This workflow has no warehouse publisher.
        registry.complete_offline(batch_id, attempt, current_accepted=accepted)
    print(registry.batch(batch_id)["status"])
```

For a held batch, inspect `registry.revisions(batch_id)` and obtain an explicit
human decision. Record it with `registry.decide(revision_id, "APPROVE" or
"REJECT", reviewer=..., rationale=..., before_hash=..., after_hash=...)` using
the exact hashes shown by the stored revision. Never automatically approve in
the pipeline. Local reviewer names are audit assertions, not authenticated
identities; access control and a reviewed approval interface are future work.

## Failure, recovery and limits

An operational failure uses `fail(batch_id, attempt, error_code="IO_FAILURE")`.
Only a non-secret uppercase code is accepted; do not pass exception messages,
connection options, credentials, environment dumps or secrets in audit text.
The durable checkpoint and decisions survive. A new `start()` creates a new
attempt and resets the active checkpoint to CLASSIFIED so guards run again.

If a worker terminates before reporting failure, RUNNING survives reopening the
file. First stop/confirm termination of that worker. Then call
`recover_interrupted(batch_id, expected_attempt_id=...)` using the stored active
attempt. Recovery records INTERRUPTED and fences that ID. Old attempts cannot
prepare, complete, fail, or recover a replacement attempt. There is no automatic
timeout takeover. SQLite transactions serialize claims and atomically persist
plans/approvals/events; failed writes roll back without replacing the original
error. Back up a closed registry before moving it; restore the complete database,
never individual status rows. Incompatible SQLite files fail without migration.

The registry does not maintain authoritative accepted data. Callers must supply
fresh accepted observations, including all seven metrics in canonical source
precision. Changes to that snapshot invalidate the stored plan at the guards;
an existing batch is never silently rebased. A reviewed replan/version workflow
remains integration work. Attempt fencing coordinates one batch on one host;
overlapping batches, direct warehouse writers and distributed publication need
additional coordination and transactional freshness checks. A future executor
must atomically publish warehouse data and its durable warehouse checkpoint;
SQLite completion cannot substitute for that transaction or resolve cloud
commit uncertainty.

## Tests

Use the dedicated `telecom_de` environment, without installing dependencies:

```powershell
conda activate telecom_de
$registryTestTemp = Join-Path (Get-Location) ('data/ingestion/registry-tests-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests/test_batch_control.py tests/test_incremental.py -q -p no:cacheprovider --basetemp $registryTestTemp
```

Tests use synthetic records, real local SQLite transactions, fault injection,
multiple connections, restart/recovery, stale attempt and stale approval checks,
NULL/precision round-trips, and network/connector guards. The live Snowflake
test is excluded from the broader offline regression command.

Verified on 2026-11-15 using `telecom_de` Python 3.11.18:

- Full offline suite: **194 passed in 3.75 seconds**, including 36 registry tests.
  Command: `python -m pytest tests --ignore=tests/test_snowflake_live.py -q
  -p no:cacheprovider --basetemp <fresh-workspace-test-directory>`.
- Actual original TRA CSV smoke test: 66 UNCHANGED, zero proposed changes,
  successful local completion and no-op completed rerun.
- The CSV SHA-256 remains
  `12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
- Fingerprints of all 34 pre-existing files under `telecom_pipeline`, `tests`,
  `sql`, `config`, and `data/raw` matched before and after development. No live
  Snowflake verification was performed.
