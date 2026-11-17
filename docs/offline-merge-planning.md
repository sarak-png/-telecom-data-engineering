# Offline approval-gated MERGE planning

`telecom_pipeline.merge_planning.build_merge_plan()` creates an immutable logical
MERGE review manifest for `TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY` using
contract 1.1, the incremental classifier and local batch registry. Its output
contains proposed INSERT/UPDATE actions, exact Decimal before/after values,
approval evidence, counts, proposed lineage and transaction/rollback requirements.
JSON output includes a deterministic SHA-256 covering the complete manifest.

This deliverable is an offline planning API. `publication_allowed` always returns
`False`. The API emits data for review and provides no warehouse connection,
SQL renderer, executor, grant management or publication command. Local registry
queries use a consistent SQLite read transaction. Planning leaves registry
statuses, attempts, events and decisions intact.

## Inputs and approval checks

Prepare the input through the existing reader/classifier and register it through
`BatchRegistry.register()` with its exact source hash and `declared_periods`.
The declared scope lists the precise input YEAR/QUARTER pairs. Both FULL_SNAPSHOT
and QUARTER_DELTA are supported. The planner requires explicit declared scope
to identify unexpected periods. Existing batches registered without scope need
a newly reviewed scope-specific intent before using this API.

Supply the same source file and a canonical accepted snapshot. The shared reader
parses and hashes one immutable byte snapshot. The planner verifies that the
file hash, canonical input rows, accepted-state fingerprint, schema version,
canonicalization version and expected policy match the registered batch.
It reclassifies the records against the supplied accepted state and rejects
conflicting duplicates, missing or unexpected declared periods, missing snapshot
history, and calendar gaps. Accepted duplicate keys also fail validation.

The planner uses policy version `1` by default. A reviewed alternative version
must be supplied through `expected_policy_version`. A registry policy beginning
`READ_ONLY_` is rejected because FLOAT_TEXT_V1 comparisons carry approximate
accepted values. Canonical accepted history must come from independently reviewed
source/audit evidence. The four-ULP comparison proposal remains unresolved.

Every REVISED record requires the latest registry decision to be APPROVE for
the exact revision ID and before/after hashes. Missing approvals and rejections
block the entire plan, including NEW records. Superseding decisions remain in
the registry. A later revocation blocks plan regeneration. The manifest records
decision ID, version, reviewer, rationale, timestamp and hashes for future
revalidation. Planning performs no automatic approval.

RUNNING and COMPLETED batches are excluded from new planning. Use pending/held
batches before claiming an execution attempt. Completed-batch recovery belongs
to the existing registry lifecycle and the future warehouse checkpoint workflow.

## Logical MERGE behavior

| Classification | Planned behavior |
| --- | --- |
| NEW | INSERT one complete quarterly record if the key remains absent |
| REVISED with approval | UPDATE all seven metrics for the matching YEAR/QUARTER after rechecking the exact accepted before-state |
| UNCHANGED | Exclude from operations and preserve existing data and lineage |
| Conflicting duplicates or rejected scope | Reject the complete plan |

YEAR and QUARTER form the business key. Operations are sorted by that key.
Each UPDATE contains the complete canonical before-image and proposed after-image
plus their hashes. NULLs remain JSON null and Python None. Numeric values persist
as canonical decimal strings with no context rounding. Approved NULL-to-value
and value-to-NULL transitions retain both versions. Delta omissions preserve
accepted records. Identical input duplicates collapse through the classifier
and remain counted in the manifest.

The quarter start date is derived from YEAR/QUARTER. The proposed SOURCE_TABLE
is `TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN` and ANALYTICS_BATCH_ID is the registered
batch ID. ANALYTICS_LOADED_AT is explicitly marked as a future runtime UTC
timestamp. Those lineage semantics require review with the future publisher.
The manifest captures the target, source table and complete contract fingerprint
at planning time.

Repeated planning with identical input, accepted state, contract and decisions
produces identical JSON and plan hash. A new equivalent file compared against an
already accepted after-state yields UNCHANGED operations. Actual warehouse rerun
handling still requires durable publication checkpoints and fresh read-back
checks before deciding whether a write already committed.

## Synthetic data and provenance

Use `synthetic=True` for invented test data. The planner also labels filenames
containing `synthetic` and paths under `fixtures` as SYNTHETIC_TEST. Other input
is labeled UNVERIFIED_SOURCE. Path-based labeling is a convenience and cannot
establish source authenticity. File hashes establish byte identity, while
publication authority requires independently reviewed provenance.

Every output is OFFLINE_REVIEW_ONLY with `publication_allowed=false`, including
plans built from the original CSV. Synthetic fixture values may appear in
offline review manifests and tests. They must never be used for publication into
the real TELECOM_DB tables. A future publisher must validate actual TRA source
evidence, reject synthetic/diagnostic approval contexts and obtain a separately
approved publication specification. A local test approval authorizes only that
offline test scenario.

## API usage

Given an existing open `BatchRegistry`, its batch ID and canonical accepted
records, build the manifest as follows. The batch must have declared scope and
all required revision approvals already recorded by the reviewer.

```python
from telecom_pipeline.merge_planning import build_merge_plan

review_plan = build_merge_plan(
    registry,
    batch_id,
    source_path=source_path,
    current_accepted=accepted_records,
    synthetic=True,  # Use for synthetic offline input.
)
print(review_plan.to_json())
```

For the existing synthetic three-quarter fixture and its synthetic baseline,
an approved plan contains one INSERT, one UPDATE and one UNCHANGED record.
Against the real historical source snapshot, that fixture would instead have
two revisions and one new quarter, so both revisions need offline test decisions
before any plan is created. These plans retain their synthetic status.

## Review before future warehouse writes

1. Review the immutable source, file hash, declared scope, provenance, canonical
   accepted evidence and plan hash. Confirm counts, keys, all seven metrics,
   NULL transitions, approved before/after values and decision versions.
2. Verify the live warehouse contract separately with approval. Resolve FLOAT
   projection/read-back policy and source authority before SQL implementation.
   The fact has no enforced primary/unique key, so the future executor must check
   duplicate target keys and coordinate overlapping publishers/manual writers.
3. Review the proposed metadata semantics and complete before-images, including
   original fact lineage. The current classifier exposes the nine source fields,
   so its before-images cannot restore the four original lineage/date columns
   by themselves. Future warehouse audit capture must preserve the full fact row.
4. Review integration across RAW, STAGING, ANALYTICS and companion audit/control
   tables. This fact-level manifest specifies candidate Analytics changes.
   The existing historical SQL retains its validated 66-row baseline.
5. Review future parameterized SQL, bound values, exact target objects, precision
   handling, concurrency controls, reconciliation and error recovery. Obtain
   explicit approval for that concrete write package before deployment.

The current live read-only command continues to retrieve its fixed SELECT and
produce diagnostic comparisons. It has no dependency on this planner.

## Transaction and rollback planning

The manifest specifies a future single warehouse DML transaction that rechecks
key uniqueness, NEW-key absence, expected-before versions and latest approvals,
applies the reviewed layer changes, reconciles all seven metrics/NULLs and
lineage, and atomically records accepted audit versions plus the warehouse batch
checkpoint before COMMIT. Session coordination must prevent stale approvals
and overlapping writers from bypassing those checks.

Any failure before commit requires full transaction ROLLBACK, retaining the
original exception if rollback cleanup also fails. The UPDATE before-images
provide evidence for reconciliation and recovery review. If commit acknowledgment
is uncertain, inspect durable warehouse state before retrying. After commit,
a compensating correction requires a new approval. Withdrawal of an incorrectly
inserted NEW period needs a separately reviewed recovery procedure.

These are transaction requirements in the review manifest. Tests validate the
planned instructions and preserved before-images. Live rollback behavior,
Snowflake SQL compilation and cross-layer atomicity require separately approved
integration testing during a later phase.

## Reproduce offline tests

From Anaconda Prompt in the project root:

```bat
conda activate telecom_de
python -m pytest tests/test_merge_planning.py tests/test_incremental_fixture.py tests/test_incremental.py tests/test_batch_control.py tests/test_warehouse_contract.py tests/test_snowflake_readonly.py tests/test_readonly_diagnostics.py -q -p no:cacheprovider --basetemp data/ingestion/merge-plan-tests-%RANDOM%-%RANDOM%
```

Tests cover insert/update selection, unchanged exclusion, NULL transitions,
approval/rejection/revocation, exact hash binding, source substitution, explicit
scope and conflicting duplicates, stale accepted state, deterministic reruns,
synthetic restrictions, rollback requirements and registry preservation. Network
access is blocked in planner tests. SQLite databases are disposable local fixtures.

Verified on 2026-11-16 in `telecom_de`. The complete offline suite, explicitly
excluding `tests/test_snowflake_live.py`, reported **302 passed in 6.94 seconds**,
including 26 new planner tests. Fingerprints of all 42 pre-existing files under
`telecom_pipeline`, `tests`, `config`, `sql` and `data/raw` matched before and
after development. The original TRA CSV SHA-256 remains
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
