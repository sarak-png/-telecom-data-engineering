# Warehouse publication readiness

Assessment date: 2026-11-16. Warehouse contract: 1.1.

**Status: warehouse publication remains blocked.** Offline validation,
classification, local approval state, review manifests and read-only diagnostics
are implemented and tested. An incremental publisher for the confirmed warehouse
architecture still requires implementation, review and live verification.

```json
{
  "publication_allowed": false,
  "readiness": "BLOCKED",
  "assessment_scope": "OFFLINE_IMPLEMENTATION_AND_RECORDED_EVIDENCE"
}
```

Checked items below identify implemented capabilities with offline test evidence.
Open items identify a proposed design, missing implementation, approval decision
or unverified live behavior. A checked local capability does not establish its
warehouse deployment. This report records readiness and authorizes no writes.

## Warehouse contract and evidence boundaries

| Object | Recorded evidence | Remaining verification |
| --- | --- | --- |
| TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE | User-supplied live DESCRIBE, nine nullable VARCHAR columns, no primary/unique key | Current metadata, accepted-current semantics, immutable-history coverage |
| TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN | User-supplied DESCRIBE export, two nullable NUMBER(38,0) keys and seven nullable FLOAT metrics | Current metadata, required key validation and current-state reconciliation |
| TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY | User-supplied live DESCRIBE, thirteen columns, seven nullable FLOAT metrics and required keys/date/lineage, no primary/unique key | Current metadata, key uniqueness, source-snapshot identity and publication lineage semantics |
| TELECOM_DB.ANALYTICS.VW_YEARLY_TELECOM_SUMMARY | User-confirmed name and local SQL projection | Live result types/nullability and post-update behavior |
| TELECOM_DB.ANALYTICS.VW_TELECOM_DATA_COVERAGE | User-confirmed name and local SQL projection | Live result types/nullability and post-update behavior |

The contract records the historical 66 consecutive periods from 2010 Q1 through
2026 Q2. Metric NULL counts in contract order are 0, 0, 4, 15, 32, 32 and 1.
The user previously reported successful manual warehouse quality validation and
historical reconciliation. These are recorded historical results. A current live
read-only report and incremental write verification are separate evidence gaps.
Older authentication records include external-browser failure and OAuth timeout.
They do not establish the outcome of a later manual OAuth attempt.

The source SHA-256 is
`12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
The historical row/NULL counts remain regression checks for this snapshot.
Future legitimate updates require batch-derived expectations.

Evidence: [warehouse contract](../config/warehouse_contract.json),
[contract explanation](warehouse-contract.md),
[manual quality findings](manual-data-quality-validation.md),
[historical connection verification](archive/phase3-verification.md).

## Source data validation

- [x] **Implemented/tested.** Strict CSV decoding, TRA comment metadata and
  bilingual headers, exact source columns, quoted numeric text and immutable
  source hashing. The shared reader supports plain CSV and literal-cell XLSX.
- [x] **Implemented/tested.** Required period keys, finite nonnegative metrics,
  NULL preservation, duplicate detection and rejection of conflicting period
  values. Canonicalization preserves source Decimal precision.
- [x] **Implemented/tested.** FULL_SNAPSHOT and QUARTER_DELTA classification,
  calendar continuity, optional exact declared scope and deterministic results.
  The MERGE planner requires declared scope and rejects scope/conflict failures.
- [x] **Implemented/tested.** The original snapshot retains 66 unique periods
  and its established NULL counts. Its metadata declares 68 records, and the
  ingestion report exposes this discrepancy with the 66 parsed rows.
- [ ] **Missing publication evidence.** Record trusted source publication/version,
  units, reviewed quarter scope and freshness expectations for the actual update.
  Byte identity alone does not establish source authority.
- [ ] **Proposed.** Add durable immutable source capture and a reconciled canonical
  accepted-history bootstrap tied to existing RAW/STAGING/ANALYTICS values.
- [ ] **Integration gap.** Register source identity and records from the same
  immutable snapshot in the future publisher. The registry alone accepts a
  caller-supplied digest; the planner additionally verifies the actual source.

Evidence: [readers](../telecom_pipeline/readers.py),
[ingestion](../telecom_pipeline/ingestion.py),
[validation](../telecom_pipeline/validation.py),
[classifier](../telecom_pipeline/incremental.py).

## FLOAT precision policy

- [x] **Implemented/tested.** Exact canonical source comparison preserves
  meaningful revisions even when before/after values share one FLOAT projection.
- [x] **Implemented/tested.** The offline assessor reports exact binary expansion,
  projection consistency, ULP distance, NULL transitions, overflow and underflow.
  ULP distance grants no acceptance tolerance.
- [x] **Implemented/tested.** Metric-specific grid assessment requires explicit
  evidence and reviewer references. Ambiguous candidates and midpoint boundaries
  remain unresolved. No authoritative TRA grids are supplied by default.
- [x] **Implemented/tested.** The conversion-validation command assesses all
  historical source metrics through the existing fixed SELECT and enforces the
  configured TELECOM_READONLY role. Mocked tests cover representative values.
- [ ] **Human decision.** Approve the reconciliation policy and verify its loading,
  casting and binary64 read-back assumptions. Document precision evidence before
  adopting any per-metric grid, rounding or tolerance. Observed two/six/zero
  decimal formatting is insufficient authority for those choices.
- [ ] **Unverified live.** Retain a real conversion-validation report and review
  every unexplained discrepancy. Matching a Python FLOAT projection cannot prove
  historical source identity because multiple decimals may map to the same FLOAT.
- [ ] **Integration gap.** Connect the approved policy to the future publisher
  and audit evidence. The offline assessor currently operates independently.

The four-ULP proposal remains unapproved and unused. The live read-only comparison
continues to use Decimal(str(float)) and reports diagnostic comparisons. Its
approximate READ_ONLY approval contexts are rejected by the offline planner.

Evidence: [precision module](../telecom_pipeline/precision.py),
[precision policy](float-precision-policy.md),
[conversion validation](../telecom_pipeline/float_validation.py).

## Approval workflow

- [x] **Implemented/tested.** SQLite stores immutable revision payloads and
  versioned decisions with timestamps, reviewer/rationale and exact before/after
  hashes. Revisions hold the complete batch until all are approved.
- [x] **Implemented/tested.** Rejections survive restart/re-registration.
  Superseding decisions preserve history. Decisions freeze during an active
  attempt and after local completion.
- [x] **Implemented/tested.** The planner checks latest approvals, source bytes,
  canonical input, accepted snapshot, declared scope and version context inside
  a consistent local SQLite read transaction. Revocation blocks regeneration.
- [ ] **Missing implementation.** Establish authenticated/restricted reviewer
  recording. The current reviewer/reference strings are caller assertions.
- [ ] **Proposed.** Revalidate approval versions, accepted-before values and
  authority immediately before a coordinated warehouse commit. A previously
  generated JSON plan can become stale after approval revocation or data changes.
- [ ] **Workflow gap.** Define explicit replanning/version handling when accepted
  history changes. Existing batch identities never silently replace stored plans.

Evidence: [batch registry](../telecom_pipeline/batch_control.py),
[offline planner](../telecom_pipeline/merge_planning.py).

## Snowflake write permissions and execution boundary

- [x] **Implemented/tested.** The read-only adapter exposes one fixed SELECT,
  requires explicit live opt-in and sanitizes runtime errors. The FLOAT validation
  CLI checks that TELECOM_READONLY is the configured role.
- [x] **Implemented/tested.** The new planner emits logical review manifests
  with publication_allowed=false. It exposes no warehouse executor or SQL renderer.
- [ ] **Unverified live.** Confirm current OAuth authentication, role availability
  and actual SELECT/USAGE access through a retained manual read-only result.
  The environment-role check does not inspect the role's real grants.
- [ ] **Future approval.** Review the precise objects and least-privilege writer
  access required by a concrete publication implementation. TELECOM_READONLY
  remains the role for read-only verification. Write access is not granted here.
- [ ] **Integration gap.** Implement an executor matching the confirmed nine-column
  RAW, nine-column STAGING and thirteen-column Analytics contracts, including
  reviewed metadata and control/audit objects.

`snowflake_loader.py` is an existing legacy RAW.TRA_TRAFFIC/OPS write-capable
executor behind its own execution approval option. Its schema and behavior differ
from the confirmed objects. The new manifests' publication_allowed field is not
a global guard around that legacy code. Keep its scope explicit and exclude it
from the new publication path until an adapter/deprecation decision is reviewed.
The historical fixed-baseline Analytics scripts likewise do not implement future
quarter/revision publication.

Evidence: [read-only adapter](../telecom_pipeline/snowflake_readonly.py),
[read-only CLI](../telecom_pipeline/readonly_dry_run.py),
[legacy executor](../telecom_pipeline/snowflake_loader.py),
[contract legacy references](warehouse-contract.md).

## Transaction safety

- [x] **Implemented/tested locally.** SQLite transactions protect registration,
  claims, decisions and event consistency. Planner reads use one consistent
  SQLite snapshot. Fault injection verifies local rollback behavior.
- [x] **Implemented/tested as a plan.** Review manifests specify expected-before
  checks, NEW-key absence, duplicate-target checks, reconciliation and commit
  requirements. These are instructions for a future executor.
- [ ] **Proposed.** Publish affected RAW/STAGING/ANALYTICS rows, accepted-version
  audit events and the durable warehouse batch checkpoint in one coordinated
  DML transaction. Review deployment DDL separately from that transaction.
- [ ] **Missing implementation.** Coordinate overlapping batches and manual
  writers across the whole publication boundary. Per-batch local attempt fencing
  supplies no multi-host or cross-batch warehouse serialization.
- [ ] **Unverified live.** Test compiled Snowflake SQL, bindings, NULL semantics,
  FLOAT read-back, duplicate matching, approval races and cross-layer rollback
  in a separately approved isolated environment. RAW/fact have no primary/unique
  key, so application key checks and coordination are required.

## Idempotency

- [x] **Implemented/tested locally.** File/mode/scope/version intent identities
  yield stable batch IDs and new attempt IDs. Duplicate input identity is reused,
  conflicting parsed records under that identity fail, and exact duplicates collapse.
- [x] **Implemented/tested locally.** Source Decimal hashes, deterministic plan
  digests, unchanged exclusion and matching accepted after-state support safe
  replay assessment. Local COMPLETED state is terminal.
- [ ] **Proposed.** Record the authoritative warehouse publication checkpoint
  atomically with the changes, then return the committed result on exact reruns.
- [ ] **Unverified live.** Prove same-file and equivalent-file reruns, replay of
  older source versions, overlapping intents and commit-acknowledgment loss
  against accepted warehouse state.

SQLite OFFLINE_COMPLETED records a local simulation. It cannot establish that a
warehouse publication committed. Stable plan IDs alone cannot prevent duplicate
cloud writes or stale revisions.

## Rollback and recovery

- [x] **Implemented/tested locally.** Explicit interrupted-attempt recovery fences
  stale workers. Retryable failure, persisted approval holds and terminal-state
  protection survive registry reopening.
- [x] **Implemented/tested as a plan.** UPDATE before-images retain all seven
  canonical metrics and NULL states. The manifest calls for full uncommitted
  rollback, preservation of the original error and durable-state inspection when
  commit acknowledgment is uncertain.
- [ ] **Missing evidence.** Capture complete original fact lineage/date metadata
  with the metric before-images. The canonical nine-column record cannot restore
  all thirteen fact fields by itself.
- [ ] **Proposed.** Retain a reviewed backup/export and test restoration and
  recovery against deployed audit/checkpoint state before the first publication.
- [ ] **Human decision.** Approve the compensating-revision procedure after commit
  and a separate withdrawal procedure for an incorrectly inserted NEW period.
- [ ] **Unverified live.** Inject failures before/after layer writes, during
  reconciliation and around COMMIT; verify authoritative outcome detection.

## Audit logging

- [x] **Implemented/tested locally.** Phase 2 emits structured JSON events. The
  SQLite registry stores manifests, attempts, checkpoints, revision details,
  versioned decisions and UTC history. Read-only errors expose safe diagnostics.
- [x] **Implemented/tested locally.** Offline plans include source/accepted hashes,
  contract fingerprint, decision evidence and a complete plan digest.
- [ ] **Proposed.** Review and deploy companion batch/record/decision audit objects.
  INGESTION_BATCHES, INGESTION_RECORD_AUDIT and REVISION_APPROVALS are design names;
  their deployment is absent from the confirmed warehouse contract.
- [ ] **Missing implementation.** Make accepted versions and commit checkpoints
  durable and atomic with warehouse changes. Record actual execution/reconciliation
  evidence when an approved executor is available.
- [ ] **Operational decision.** Define access, retention, backup and integrity
  protection for source archives and audit records. Append-only behavior through
  the local API does not protect SQLite files against direct modification.

## Synthetic data protection

- [x] **Implemented/tested.** The CSV fixture explicitly labels invented metrics.
  Its expected NEW/REVISED/UNCHANGED outcomes are against a synthetic baseline.
- [x] **Implemented/tested.** Planner output labels known fixture paths/names or
  explicit synthetic input as SYNTHETIC_TEST. Other input is UNVERIFIED_SOURCE.
  Publication stays false for every offline plan and precision/validation report.
- [x] **Implemented/tested.** Read-only comparison and validation issue the fixed
  SELECT and have no publication action. Synthetic local approvals stay test evidence.
- [ ] **Future executor requirement.** Verify trusted source provenance and reject
  synthetic/diagnostic evidence before publication. Filename heuristics are
  insufficient when data can be renamed or copied. Require a separately reviewed
  publication specification instead of trusting editable JSON flags.

Evidence: [synthetic fixture](../tests/fixtures/synthetic_tra_incremental.csv),
[fixture tests](../tests/test_incremental_fixture.py),
[planner review procedure](offline-merge-planning.md).

## Recommended completion order

1. Retain current manual read-only contract/conversion evidence and resolve any
   discrepancies. Reconcile the canonical historical baseline and actual source
   provenance. Keep historical and future-batch expectations distinct.
2. Approve the precision/reconciliation rule, reviewer authority, exact source
   scope, fact lineage semantics and publication coordination model.
3. Prepare and review the confirmed-object executor and companion audit/control
   definitions, including parameterized DML, complete before-images and durable
   checkpoint handling. Review exact write permissions and deployment separately.
4. After explicit approval of an isolated target and operations, test actual
   transaction safety, idempotency, source protection, rollback and recovery.
5. Review that evidence, source update and concrete publication package before
   any real warehouse update. Leave publication_allowed=false until a separately
   approved implementation and deployment establishes its execution boundary.

Scheduling is outside this assessment and is not a prerequisite for a reviewed
manual publication workflow.

## Offline test evidence

Run on 2026-11-16 using Python 3.11 in `telecom_de`:

**363 passed in 6.08 seconds.** The live warehouse write test was explicitly
excluded by path. The suite includes two subprocess checks of the FLOAT validation
module entry point, real disposable SQLite transactions and mocked Snowflake
connections. No live connection was used for this assessment.

| Coverage | Existing test files |
| --- | --- |
| Source parsing, local ingestion and transformations | test_ingestion.py, test_pipeline.py, test_transformation.py |
| Contract and legacy adapter configuration | test_warehouse_contract.py, test_snowflake.py, test_auth_check.py |
| Incremental classification and synthetic fixture | test_incremental.py, test_incremental_fixture.py |
| Registry approvals, recovery and offline MERGE planning | test_batch_control.py, test_merge_planning.py |
| Precision and read-only diagnostics/validation | test_precision.py, test_snowflake_readonly.py, test_readonly_diagnostics.py, test_float_validation.py |

From Anaconda Prompt in the project root:

```bat
conda activate telecom_de
python -m pytest tests --ignore=tests/test_snowflake_live.py -q -p no:cacheprovider --basetemp data/ingestion/readiness-tests-%RANDOM%-%RANDOM%
```

These results establish the local behavior described above. SQL compilation,
current cloud privileges, real FLOAT conversion outcomes, warehouse atomicity
and production recovery remain unverified until separately approved evidence
is recorded. Prior user-reported historical validation retains its stated scope.
