# Automation implementation plan

Prepared on 2026-11-15 against warehouse contract 1.1 and the approved direction
in [the incremental loading design](incremental-loading-design.md).

The implementation retains the existing RAW.TRA_TRAFFIC_SOURCE,
STAGING.TRA_TRAFFIC_CLEAN, and ANALYTICS.FACT_TELECOM_QUARTERLY objects. It uses
YEAR/QUARTER business keys, canonical Decimal change detection, explicit snapshot
and delta modes, preserved accepted NULLs, and whole-batch holds for historical
revisions awaiting approval. Four-ULP FLOAT tolerance remains a candidate that
must earn acceptance through evidence. Scheduling follows this implementation
as a separately scoped phase.

This document defines future deliverables and review boundaries. The paths listed
below are planned changes. Only this plan is created in the present task.

## Phase 1 — Offline policy and regression foundation

Define one incremental policy version and retain contract 1.1 as the existing
warehouse evidence. Record input mode and declared period scope explicitly.
Keep the original CSV at its current path as the immutable historical fixture.
Generate future updates as clearly labeled synthetic test fixtures.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 1.1 | config/incremental_policy.json | Create versioned dataset ID, FULL_SNAPSHOT/QUARTER_DELTA scope rules, canonicalization version, reviewer policy, archive/checkpoint locations, and provisional precision settings |
| 1.2 | config/warehouse_contract.json | Add a reviewed control_objects registry with explicit proposed/deployed evidence status, retaining the existing five core-object definitions |
| 1.3 | tests/conftest.py | Create shared original-fixture access and labeled synthetic factories for a 67th quarter, quarter-only deltas, corrections, duplicates, gaps, and NULL transitions |
| 1.4 | tests/test_warehouse_contract.py | Extend checks for the approved companion contracts and maintain existing object/column/type assertions |
| 1.5 | tests/test_ingestion.py | Extend snapshot integrity/format checks using the shared fixture access while retaining the original source fingerprint assertions |

Acceptance requires the original 66 unique consecutive periods from 2010 Q1
through 2026 Q2 and metric NULL counts 0, 0, 4, 15, 32, 32, and 1 to pass.
Proposed companion metadata must carry an explicit proposal/evidence status.
The policy must distinguish historical fixture expectations from future batch
expectations. All policy and fixture tests run locally.
The control_objects registry keeps proposed companion definitions outside the
existing core objects collection used by the offline confirmed-object report.
Contract version changes are recorded with the reviewed registry update.

## Phase 2 — Offline classification, approvals, and recovery model

Reuse the established parsing and validation code. New APIs expose immutable
source snapshots and canonical candidates while preserving existing callers'
behavior. Establish the planner and control-state model before connecting a
warehouse executor.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 2.1 | telecom_pipeline/readers.py | Add an immutable snapshot API exposing the exact bytes/hash used for parsing, retain read_source compatibility |
| 2.2 | telecom_pipeline/validation.py | Reuse/extract row/schema validation for classification while retaining validate's current default behavior for existing callers |
| 2.3 | telecom_pipeline/incremental.py | Create pure canonicalization, stable file/intent/batch/revision identities, scope validation, and NEW/UNCHANGED/REVISED/CONFLICTING_DUPLICATE planning |
| 2.4 | telecom_pipeline/precision.py | Create finite Decimal-to-binary64 projection and configurable read-back assessment, with four ULPs labeled provisional |
| 2.5 | telecom_pipeline/batch_control.py | Create manifest state transitions, append-only approval/version events, stale-decision checks, and retry/uncertain-commit decisions |
| 2.6 | telecom_pipeline/incremental_cli.py | Create an explicit offline plan command using the shared contract, policy, readers, and planner |
| 2.7 | tests/test_incremental.py | Create canonicalization, identity, classification, duplicate, scope, replay, and NULL-policy tests |
| 2.8 | tests/test_precision.py | Create tests across magnitudes, zero, NULL, subnormal boundaries, overflow/underflow, and source changes sharing a FLOAT projection |
| 2.9 | tests/test_batch_control.py | Create approval binding, rejection/reopening, checkpoint, lost-acknowledgment, and committed-state protection tests |
| 2.10 | tests/test_transformation.py | Extend checks showing original local Decimal exports retain their established behavior alongside the new mapping adapter |

Acceptance requires equivalent full-snapshot and delta fixtures to yield the
same proposed current state. An unchanged file/intent must retain its batch ID.
A new attempt gets its own attempt ID. Identical duplicate rows must be counted
and collapsed, conflicting duplicates must reject publication, and revisions
must place the complete batch in WAITING_APPROVAL. Pending or stale decisions
must be insufficient to publish a revision. NULL and zero must remain distinct.

The offline CLI must display the selected scope, proposed changes, evidence,
and precision policy. Its tests must fail if planning attempts a connection or
SQL execution. Failures are simulated with in-memory state and labeled fixtures.

Four-ULP testing compares source Decimal projections with candidate FLOAT
read-back values. Exact zero and NULL behavior is mandatory. Canonical change
hashes must remain independent of any FLOAT tolerance. Record measured error
distributions and boundary failures before recommending a final threshold.

## Proposed audit and control tables

The following three companion objects follow the design's proposed RAW-schema
placement. They supplement the existing current-value tables. Exact deployment
definitions, privileges, and retention settings enter the approval package in
Phase 3.

| Proposed table | Logical identity and responsibilities |
| --- | --- |
| TELECOM_DB.RAW.INGESTION_BATCHES | One manifest per deterministic batch_id, with intent hash, dataset/file identity, archived-file reference, input mode/scope, contract/policy versions, durable phase, UTC times, counts, and terminal publication outcome |
| TELECOM_DB.RAW.INGESTION_RECORD_AUDIT | Stable event identity for original source occurrences, classifications, and accepted versions, with explicit event kind, row ordinal, YEAR/QUARTER, original/canonical payloads, hashes, before/after links, effective batch/time, and prior fact lineage |
| TELECOM_DB.RAW.REVISION_APPROVALS | Append-only revision decision identity/version with batch, key, exact expected-before/proposed-after hashes, reviewer, rationale, decision, UTC time, and superseded-decision link |

Audit event kinds distinguish SOURCE_ROW, CLASSIFICATION, and ACCEPTED_VERSION.
SOURCE_ROW identity includes batch and parsed ordinal. Classification history
records its comparison context so a re-plan can append a new event. Accepted
version identity binds the key and exact before/after hashes. Only committed
accepted-version events determine the authoritative current canonical value.
An unchanged observation can reference the accepted version without creating a
new business version. Original occurrences and decisions retain their history.

The batch manifest holds the authoritative checkpoint. Attempt IDs and bounded
attempt summaries link to durable structured event logs. Failure logging must
preserve a COMMITTED checkpoint. UNKNOWN_COMMIT describes an uncertain attempt
until durable state can be inspected. Plain retry activity cannot reopen a
rejected approval decision.

Serialized publishing and explicit uniqueness checks enforce logical identities.
The initial deployment admits one publisher for this dataset. Multi-host
publication requires separately tested shared coordination. Control-table
constraints alone are insufficient evidence of duplicate prevention.

## Phase 3 — Offline warehouse adapter and deployment package

Prepare parameterized SQL drafts and an executor against the actual object
contracts. Keep transaction ownership in the new Python publisher. The historical
SQL and metadata-dependent RAW/OPS executor remain available as regression
references. The new entry point selects the reviewed incremental path explicitly.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 3.1 | sql/incremental/001_create_control_tables.sql | Future DDL draft for the three companion tables and their documented logical identities |
| 3.2 | sql/incremental/002_capture_batch.sql | Future parameterized DML for manifests, original occurrences, classification events, and durable pre-publication checkpoints |
| 3.3 | sql/incremental/003_read_batch_state.sql | Future read queries for manifest state, accepted versions, applicable approvals, and current-value consistency |
| 3.4 | sql/incremental/004_publish_raw.sql | Future RAW MERGE using canonical business keys and accepted source-text values, applying NEW and approved REVISED records |
| 3.5 | sql/incremental/005_publish_staging.sql | Future STAGING MERGE using the exact nine confirmed column aliases and types |
| 3.6 | sql/incremental/006_publish_analytics.sql | Future fact MERGE with quarter date and accepted-publication lineage, retaining metadata on UNCHANGED rows |
| 3.7 | sql/incremental/007_reconcile_batch.sql | Future affected-key/value/NULL/lineage checks and global uniqueness checks, with batch-derived count expectations |
| 3.8 | sql/incremental/008_commit_audit.sql | Future accepted-version events and guarded COMMITTED manifest transition within the publication transaction |
| 3.9 | sql/incremental/009_record_approval.sql | Future append-only reviewer decisions with exact revision context and decision version |
| 3.10 | telecom_pipeline/incremental_loader.py | Create capture, approval persistence, baseline registration, atomic publication, reconciliation, and recovery using the planner/control model and reviewed templates |
| 3.11 | telecom_pipeline/snowflake_config.py | Extend validated target resolution for explicit environments and least-required approved connection settings |
| 3.12 | telecom_pipeline/incremental_cli.py | Extend the new command interface for review, read-only inspection, baseline registration, approval recording, publishing, and recovery with explicit operation selection |
| 3.13 | tests/test_incremental_loader.py | Create fake-connection tests for parameter binding, target scope, transaction ordering, rollback, approval checks, and retry outcomes |
| 3.14 | tests/test_incremental_sql.py | Create SQL contract checks for exact mappings, source uniqueness, permitted write targets, NULL handling, and exclusion of DDL from publication |
| 3.15 | docs/incremental-loading-runbook.md | Create operation, decision-recording, recovery, and deployment instructions tied to the reviewed commands and templates |

These SQL files are future implementation deliverables. This planning task
creates none of them. A later offline-development task can author them for review
before any warehouse submission.

The publisher uses one connection and one explicit DML transaction for RAW,
STAGING, ANALYTICS, accepted audit events, and the COMMITTED manifest result.
Immutable capture has its own earlier durable checkpoint. Immediately before
publishing, recheck accepted-before versions and applicable approvals. On failure,
roll back that publication transaction and preserve the original exception even
if rollback cleanup fails. On uncertain commit, inspect durable state before
any repeated publication writes.

RAW keeps its nine-column schema. Companion records preserve every received
snapshot and correction. Current rows change only for NEW or approved REVISED
observations. The proposed existing fact batch/time semantics identify the
latest accepted materialization and retain earlier lineage in the audit history.

Acceptance requires fault-injection tests after each write boundary and before/
after commit acknowledgment. Reruns must create zero duplicate current keys or
accepted versions. Unchanged observations retain metrics and prior lineage.
Baseline registration must write only control/audit metadata after validating
existing values. Legacy tests must retain their original fixture expectations.
The offline test suite must have an explicit selection that excludes live tests.

## Phase 4 — Snowflake read-only verification

This phase starts only when a working approved connection is available and
read-only verification is authorized. Its operations inspect the existing
warehouse and return a review report. Environment selection must distinguish
the current warehouse from any later isolated testing target.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 4.1 | telecom_pipeline/warehouse_verification.py | Create read-only object/column/type/nullability/key inspection and baseline/drift assessment with sanitized diagnostics |
| 4.2 | tests/test_warehouse_verification.py | Create fake-metadata and read-only-query tests, including wrong object, precision, nullability, and duplicate-key cases |
| 4.3 | pytest.ini | Declare separate read-only and isolated-write integration markers for deliberate test selection |
| 4.4 | tests/test_incremental_integration.py | Create opt-in integration cases with separate read-only and write-test selections, explicit target allowlists, and synthetic-fixture labeling |
| 4.5 | config/warehouse_contract.json | Update only from verified inspection evidence, with version/evidence history when the contract changes |
| 4.6 | docs/warehouse-contract.md | Record verified evidence and remaining differences |
| 4.7 | docs/incremental-loading-runbook.md | Record approved role, environment, exact verification commands, results, and deployment prerequisites |

Acceptance requires the actual table shapes to match the contract and view
metadata gaps to be resolved or explicitly reviewed. Verify 66 distinct periods,
the known coverage, all seven per-metric NULL counts, and cross-layer values
using the provisional precision assessment. Confirm the archive/source hash used
for bootstrap and document drift or missing evidence before the write-test phase.

This read-only phase assesses available privileges and target definitions. It
provides no evidence that a proposed DDL or MERGE has executed successfully.

## Phase 5 — Approved isolated Snowflake verification

Obtain approval for the exact isolated environment, companion-table DDL, loading
statements, fixtures, and cleanup scope before running write tests. Isolated test
writes are a distinct permission boundary from Phase 4 read-only verification.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 5.1 | config/incremental_policy.json | Record explicitly selected isolated targets and reviewed precision-policy version |
| 5.2 | tests/test_incremental_integration.py | Complete compilation, real transaction, replay, approval, concurrency, NULL, and FLOAT-boundary scenarios against approved isolated targets |
| 5.3 | tests/test_precision.py | Add reproducible boundary cases identified by actual connector/warehouse read-back evidence |
| 5.4 | docs/incremental-loading-runbook.md | Record actual SQL/query IDs, result counts, numeric evidence, rollback observations, and accepted comparison policy |

Reuse the Phase 3 templates with validated explicit target configuration. The
isolated fixture setup and cleanup belong to the opt-in integration test harness
and its approved resource scope. Avoid blind replacement of database names in
SQL strings. Run every successful input twice and compare equivalent full and
delta executions.

Acceptance requires a synthetic 67th quarter to produce exactly one new key,
approved corrections to update one current key with complete audit evidence,
and unapproved/declined/stale corrections to hold publication. Include identical
and conflicting duplicates, formatting-only changes, NULL transitions, changed
source precision sharing a FLOAT projection, and old-file replay after revision.

Inject failures after each current-layer write and during audit/checkpoint
updates. Verify rollback across all current layers. Simulate a successful commit
whose acknowledgment is lost and verify that the next attempt recognizes it.
Test the publisher coordination boundary and approval revocation race controls.

Four ULPs can be accepted, tightened, replaced, or left unresolved based on the
measured error distribution and domain requirements. Document the justification
and prove that classification uses canonical source payloads independently.
Live publication requires an approved comparison policy with passing evidence.

## Phase 6 — Approved live deployment and manual operation

Review the concrete DDL/DML, read-only verification report, isolated test evidence,
role/privilege requirements, backup plan, and recovery procedure. Obtain approval
for those specific live operations. The approved design direction alone does
not grant permission to submit deployment statements.

| Order | Exact file | Action and responsibility |
| --- | --- | --- |
| 6.1 | sql/incremental/001_create_control_tables.sql | Deploy the reviewed companion definitions using a controlled migration step |
| 6.2 | config/warehouse_contract.json | Record the approved deployed control-object contract and its verified evidence |
| 6.3 | config/incremental_policy.json | Select the approved live targets, archive locations, precision policy, and publication scope |
| 6.4 | telecom_pipeline/incremental_cli.py | Use the reviewed commands to register the reconciled baseline and publish the first approved genuine TRA update |
| 6.5 | .env.example | Document only non-secret configuration variable names and usage for the new entry point |
| 6.6 | README.md | Link the accepted contract, manual commands, test evidence, and runbook |
| 6.7 | docs/warehouse-contract.md | Record companion-table deployment evidence and resolved adapter decisions |
| 6.8 | docs/incremental-loading-runbook.md | Record the deployed version, baseline registration, first genuine batch, recovery evidence, and handover |

Baseline registration reuses capture and accepted-audit templates, records the
actual registration time, and retains historical ingestion times as unknown
when evidence is absent. The current 66-row tables are read and reconciled during
that registration. Original source artifacts remain accessible by checksum.

Acceptance requires a committed bootstrap manifest tied to the validated fixture
and a retained pre-deployment backup/export with lineage. For the first genuine
update, reconcile every affected key/value/NULL state, new/revised/unchanged
counts, table row-count deltas, fact metadata, and view results. Rerun its exact
intent and verify an idempotent result. Synthetic quarter/revision examples are
restricted to the isolated tests. If a genuine update is unavailable, report
baseline readiness and defer the first-publication milestone.

Existing historical SQL, including sql/analytics/003_load_fact.sql, remains a
versioned historical-snapshot path. New incremental publication uses the reviewed
new templates. Existing snowflake_loader.py and its RAW/OPS tests retain their
historical adapter scope until a separately reviewed deprecation decision.

## Rollback and recovery procedures

| Failure point | Procedure and completion criterion |
| --- | --- |
| Offline implementation regression | Revert the affected code/config release to the reviewed revision, rerun the historical fixture and contract tests, and retain diagnostic artifacts |
| Read-only verification finds drift | Record the discrepancy, freeze publication readiness, and agree a correction or contract update before baseline registration |
| Partial control-table deployment | Inspect created objects against the approved migration version, retain existing data, and complete or repair only the reviewed missing/incompatible definitions through a new approved migration |
| Failure after durable source capture | Retain the archive and capture checkpoint, fix the cause, and retry the same intent with a new attempt ID |
| Failure during publication before commit | Roll back RAW, STAGING, ANALYTICS, accepted-version events, and the commit checkpoint together, retain the prior durable capture, and log the original error |
| Rollback cleanup failure or lost connection | Preserve the originating error, inspect transaction/batch state after connectivity returns, and keep publication paused until resolution |
| Commit acknowledgment lost | Read the authoritative manifest and accepted-version events, return the committed outcome if present, and retry only after establishing that publication did not commit |
| Application rollback after deployment | Disable the new publication entry point, restore the reviewed application/config release, and retain audit tables and committed history for diagnosis |
| Wrong committed REVISED value | Prepare a compensating revision from audited before values, obtain approval against the current accepted hash, and publish it as another auditable change |
| Wrong committed NEW period | Stop publication and review a withdrawal/removal procedure with explicit affected-key approval, because the current design has no automatic deletion policy |

DDL deployment is reviewed independently from publication rollback. Preserve
control/audit history when recovering the application. Any later cleanup or
restoration affecting live objects requires a concrete reviewed operation.
The companion audit history and archived sources must support recovery tests
before the first live update is accepted.

## Remaining decisions and risks

The user has approved the overall direction, including revision holds, Decimal
change detection, both input modes, and NULL preservation. The remaining items
concern concrete implementation or deployment evidence.

| Item | Resolution needed | Phase affected |
| --- | --- | --- |
| Companion definitions and ownership | Approve names, columns/event kinds, identities, grants, retention, backup, and archive access | Offline contract drafting, then deployment approval |
| Current RAW behavior | Confirm the design's accepted-current projection and complete immutable-history coverage before enabling its publisher | Adapter review before isolated/live publication |
| FLOAT comparison | Justify the final conversion/read-back policy through local boundaries and actual isolated Snowflake results | Precision implementation and isolated exit criteria |
| Source scope and freshness | Agree trusted publication metadata, full-snapshot scope, release expectations, and gap/stale-file handling | Policy configuration and first genuine update |
| Review authority | Identify the initial reviewer and authenticated decision-recording mechanism, including supersession and revocation controls | Approval implementation and revision publication |
| Fact metadata meaning | Confirm latest accepted materialization semantics for the existing batch/time columns and preservation of prior lineage | Analytics adapter approval |
| Publisher coordination | Prove one authorized publisher for the initial environment and identify any manual writer interaction | Isolated and live deployment |
| Authentication and privileges | Establish a working approved connection with sufficient privileges for each scoped phase | Read-only verification onward |
| Post-commit withdrawal | Agree recovery for a wrongly accepted NEW key that requires removal | Recovery approval before live acceptance |
| Legacy paths | Keep their historical target/schema scope explicit so operators select the reviewed new entry point | Documentation and deployment handover |

RAW/fact currently have no declared primary or unique key. STAGING metadata and
view-result evidence must be checked against their supplied/available definitions.
The old whole-table 66-row and fixed NULL assertions remain historical checks.
The incremental path uses batch-derived reconciliation and preserves the original
fixture. SQL compilation, DDL transaction behavior, MERGE matching, FLOAT
round-trips, and permissions require actual approved isolated verification.

The plan's acceptance sequence is offline correctness, read-only evidence,
approved isolated write tests, then approved live deployment and manual
publication. Future scheduling can build on that verified command interface.
