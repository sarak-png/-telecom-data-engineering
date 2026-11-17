# Incremental loading design

Prepared on 2026-11-15 against warehouse contract version 1.1. This document
proposes the next implementation and its approval decisions. All behavior
described below is proposed. The canonical object and column mapping is recorded
in [the warehouse contract](../config/warehouse_contract.json).

The validated historical fixture contains 66 quarters from 2010 Q1 through
2026 Q2. Its seven metric NULL counts are 0, 0, 4, 15, 32, 32, and 1 in contract
order. Retain this fixture as a regression reference. Future batches obtain
their expectations from declared input scope and observed accepted keys.

## Warehouse grain and publication model

For this single Oman TRA traffic dataset, the business key is `(YEAR, QUARTER)`.
Both fields are required, integral, and validated before publication. Quarter
must be between 1 and 4. Each current table will contain at most one accepted
observation for each normalized business key.

| Existing object | Proposed role |
| --- | --- |
| TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE | Current accepted source-text projection, retaining its exact nine columns |
| TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN | Current accepted typed observation per period, using its confirmed abbreviated names and FLOAT metrics |
| TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY | Current accepted quarterly fact with derived date and Analytics lineage |

The proposed RAW current-state behavior needs approval. The existing nine-column
table cannot identify repeated snapshots or versions by itself. Preserve complete
file and row history in immutable archived CSVs and proposed companion audit
storage. The three existing tables then represent the accepted current values.
Historical revisions remain retrievable through the immutable audit records.

Identical logical values with different text formatting leave current rows intact.
The audit capture preserves the newly received text. For example, source Year
`2,026.00` and `2026` share a business key after valid numeric normalization.

## Full snapshots and quarter-only deltas

Input mode is declared in the batch manifest. Row count and filename supply
observations, while the declared mode determines coverage requirements.

| Mode | Required scope | Omission behavior |
| --- | --- | --- |
| FULL_SNAPSHOT | Entire published history for this dataset | Missing previously accepted keys fail completeness validation and require investigation |
| QUARTER_DELTA | Explicit set or interval of supplied quarterly observations | Periods outside the declared scope retain their current accepted state |

Both modes use the exact nine source columns from the contract. A delta contains
a complete observation for each included quarter. Every metric cell participates
in comparison, including NULL cells. A missing metric column is a schema error.
Empty metric cells have their established unavailable-value meaning.

For a full snapshot, validate that existing accepted keys are present and that
the calendar is consecutive between the combined accepted/input endpoints.
For a delta, validate its declared keys and the resulting combined calendar.
An unexpected gap goes to review. An arrival of Q4 while Q3 is absent must be
reported explicitly. Publication-period checks use a configurable release
calendar or accepted scope to detect implausible future periods. Source maxima
and file timestamps alone cannot prove that a release is complete or current.

Source omissions do not authorize deletions. Retirement, withdrawn periods,
changed dataset coverage, and changed units require a separate reviewed policy.
An older full snapshot missing newer accepted periods is flagged as stale.

## Canonical values and record classification

Read each file once as an immutable byte snapshot. Apply the contract's explicit
column mapping, strict structure checks, numeric validation, and required-key
checks. Keep original strings in the audit capture.

Canonicalization validates thousands separators before removing them, trims
permitted whitespace, and parses finite nonnegative Decimal values. It removes
insignificant fractional zeros. Canonical zero has one representation. NULL is
encoded as JSON null. A real zero remains a numeric observation. Schema changes,
unsupported numeric representations, and invalid values fail validation with
actionable diagnostics.

Hash a deterministic UTF-8 JSON representation containing the dataset identifier,
YEAR, QUARTER, and the seven metric names/values in a fixed documented order.
Decimal measurements serialize as canonical strings to retain exact precision.
Version the canonicalization rules and include their version in batch identity.

The current warehouse metrics are FLOAT. Generate change hashes from canonical
source values before conversion. Maintain the last accepted canonical payload
and hash in the audit registry. A precision-only source change can be REVISED
even when both values project to the same FLOAT. Record that distinction and
retain both source versions. FLOAT storage and source-level change detection
therefore require separate reconciliation expectations.

For review, the proposed read-back conversion rule compares each finite warehouse
FLOAT with the expected binary64 projection of its canonical Decimal. Require
exact zero when the expected value is zero. For a positive expected value, allow
at most four units in the last place of that expected binary64 value. Reject
overflow, non-finite projections, and nonzero source values that underflow to
zero. This is an engineering proposal for approval and isolated testing. NULL
comparisons remain exact, and the tolerance never participates in classification
hashes. Direct FLOAT copies from STAGING to ANALYTICS use exact NULL-safe equality.

| Classification | Definition | Proposed handling |
| --- | --- | --- |
| NEW | Valid key absent from the accepted registry and current curated tables | Insert through all three current layers after batch checks pass |
| UNCHANGED | Existing key and identical canonical payload | Retain current values and first-recorded metadata, record the observation in the batch audit |
| REVISED | Existing key with any changed canonical metric, including NULL transitions | Create a pending before/after revision and wait for explicit approval |
| CONFLICTING_DUPLICATE | Same key has multiple distinct canonical payloads in one file | Reject the entire batch for publication and retain source/audit evidence |

Repeated rows with identical canonical payloads are collapsed to one candidate
per key. Count the extra rows and retain their source ordinals. Each resulting
candidate then receives NEW, UNCHANGED, or REVISED classification. Conflicting
duplicates are detected before a MERGE source is constructed. Snowflake can
insert multiple duplicate source rows for an unmatched key, so source uniqueness
must be established explicitly. [Snowflake MERGE behavior](https://docs.snowflake.com/en/sql-reference/sql/merge)

This initial design proposes holding the entire batch when any revision awaits
approval. Accepted rows from that batch publish together. A declined revision
rejects the batch's publication. This policy keeps one approval scope and one
atomic current-state change, and requires the user's approval before implementation.

## Historical NULLs and correction approval

Preserve each accepted historical NULL through RAW, STAGING, and ANALYTICS.
Apply no zero-filling, interpolation, carry-forward, or automatic retention of
an old non-NULL value when an incoming observation explicitly supplies NULL.

A NULL-to-value or value-to-NULL transition is a revision. Once approved, its new
current value is published and its previous NULL/value remains in audit history.
A formatting-only difference is UNCHANGED. Conversion errors are reported as
validation failures and cannot be used to manufacture source NULLs.

Each pending correction records the business key, source file/publication
reference, affected metric names, old and new canonical payloads and hashes,
their warehouse projections, and the reason for review. The approval record
contains reviewer identity, decision, UTC decision time, rationale, batch ID,
and the precise expected-before/proposed-after hashes.

The user acts as reviewer initially. A later delegated reviewer requires an
explicit authorization decision. The applying process rechecks the accepted
before hash immediately before publication. A changed before hash invalidates
the approval and returns the candidate for review. A replacement CSV or changed
policy/canonicalization version also requires a matching approval context.
File arrival time alone does not establish revision authority.

A reviewer may append a superseding decision before publication, with an explicit
reason and decision version. Reopening a rejected batch requires that recorded
review action. Ordinary retries retain the recorded rejection. Publication checks
the latest applicable decision and detects revocation before committing.

Approvals and revision history are append-only. A reversal is another audited
and approved revision. Identical retries reuse the same approved revision
identity and cannot create another accepted version of the same change.

## Identity, metadata, and audit storage

The proposed dataset identifier is `oman_tra_traffic`. The versioned intent
contains the dataset ID, raw file SHA-256, input mode, declared scope, warehouse
contract version, canonicalization version, and validation-policy version.

| Identifier | Proposed definition and use |
| --- | --- |
| source_file_sha256 | SHA-256 of exact source bytes, independent of filename |
| intent_hash | SHA-256 of deterministic JSON for the processing intent |
| batch_id | UUIDv5 using a fixed documented namespace and intent_hash, stable across identical retries |
| attempt_id | UUIDv4 for each invocation or retry |
| payload_hash | Versioned canonical key/metric hash for change comparison |
| revision_id | Deterministic hash of batch ID, key, expected-before hash, and proposed-after hash |

Record actual UTC timestamps for receipt, validation, approval, publication,
and failures. Preserve source publication dates as separately attributed metadata
when available. Historical ingestion times that were never captured stay unknown.
Code/build identity is recorded with each attempt for diagnosis.

Proposed companion tables live in RAW to retain the existing three-schema layout.
Their names and deployment require approval. Their roles are as follows.

| Proposed object | Required information |
| --- | --- |
| RAW.INGESTION_BATCHES | Batch/intent/file identities, immutable archive reference, input scope/mode, contract/policy versions, status, durable checkpoints, observed counts, attempt summaries, current publication result |
| RAW.INGESTION_RECORD_AUDIT | Original nine-column payload and row ordinal, canonical payload/hash, classification, before/after version references, accepted flag, revision identity, effective batch and time |
| RAW.REVISION_APPROVALS | Revision/batch identity, key, expected-before/proposed-after hashes, reviewer, decision, rationale, and UTC timestamp |

The accepted record version for a business key is derived from committed audit
events. An index or current-version projection can be evaluated later if scale
requires it. The audit journal retains original occurrences as well as logical
versions, with record purpose explicit. Application identities distinguish a
captured observation from an accepted business version.

Existing fact SOURCE_TABLE continues to identify the STAGING relation. Proposed
semantics for ANALYTICS_BATCH_ID and ANALYTICS_LOADED_AT identify the latest
accepted materialization of each row. NEW rows set them, UNCHANGED rows retain
them, and approved REVISED rows advance them. Previous lineage is retained in
the audit history. This semantic decision needs approval before implementation.

## Baseline registration and input flow

Before incremental publication, register the historical fixture as a bootstrap
batch using the existing original CSV hash and recorded manual validation.
Reconcile its 66 keys, metric values, and individual NULL counts against all
three current tables during the separately approved implementation phase.
Seed canonical accepted versions only after this reconciliation succeeds.
The bootstrap records the actual registration time and the provenance of the
historical evidence. It does not backdate an invented source ingestion event.

After bootstrap, each file follows this proposed sequence.

1. Archive its exact bytes under a content-addressed path and register the intent.
   Find an existing batch before doing data writes.
2. Capture original rows and metadata in the immutable audit area and commit
   that capture checkpoint. Preserve malformed-file evidence for investigation.
3. Validate structure, schema, required keys, periods, numeric values, and input
   scope. Count identical duplicate extras and reject conflicting duplicates.
4. Compare canonical candidates with the latest committed accepted versions.
   Check that current warehouse values still agree with their accepted registry.
   Produce the classification and proposed-change report.
5. Obtain approvals for revisions and validate their expected-before versions.
6. Publish all approved candidates through RAW, STAGING, and ANALYTICS in one
   explicit DML transaction. NEW rows insert, UNCHANGED rows stay intact, and
   approved REVISED rows update the matching key using NULL-safe comparisons.
7. Reconcile affected keys, values, NULL states, quarter dates, and lineage.
   Commit accepted audit events and the COMMITTED batch checkpoint in the same
   transaction as the three current tables.

The durable RAW audit capture remains available when a publication attempt rolls
back. Retry from the captured immutable input. The existing source-only RAW
table participates in current-state publication as proposed above.
Disagreement between accepted audit versions and the current tables is reported
as warehouse drift for reviewed reconciliation before publication resumes.

## Checkpoints, retries, and consistency

| Durable checkpoint/status | Meaning | Resume behavior |
| --- | --- | --- |
| RECEIVED | Manifest and archive identity registered | Verify archive checksum before capture |
| CAPTURED | Immutable source occurrences stored | Reuse captured bytes/rows for validation |
| VALIDATED | Input passed its versioned contract | Reclassify against current accepted versions |
| WAITING_APPROVAL | Revisions and report are available | Collect decisions and recheck before hashes |
| READY_TO_PUBLISH | Validated candidates have valid decisions | Revalidate current accepted versions inside the publication transaction |
| COMMITTED | Current layers, accepted audit events, and batch result committed | Return the recorded outcome and add a retry-attempt log |
| FAILED_VALIDATION / REJECTED_CONFLICT / REJECTED_REVISION | Deterministic input or review failure | Preserve evidence and require a corrected input or changed decision context |
| RETRYABLE_ERROR | A recoverable operational failure occurred | Retry from the last confirmed durable checkpoint |
| UNKNOWN_COMMIT, attempt outcome | Commit acknowledgment was lost | Inspect the batch checkpoint and accepted versions before deciding whether to retry |

Transient attempt state such as PUBLISH_STARTED belongs in attempt logs. A
saved classification can become stale during a pause, so classification and
approval applicability are checked again at publication. Advance freshness and
latest-accepted-period tracking only after the committed checkpoint.

An uncertain attempt is recorded separately from the authoritative publication
checkpoint. A committed batch checkpoint is terminal. Failure logging must
never downgrade COMMITTED to FAILED or UNKNOWN_COMMIT after a lost acknowledgment.
This rule allows the next attempt to recognize the successfully committed batch.

Run one publisher per dataset initially. Deduplicate source keys and validate
target uniqueness on every publication. Standard Snowflake table primary/unique
keys do not enforce this guarantee. Local process locks coordinate one host.
A multi-host deployment needs a tested shared coordination mechanism before
parallel publishers are permitted. [Snowflake key constraints](https://docs.snowflake.com/en/user-guide/table-considerations)

Use one connection and explicit transaction for current-layer changes, accepted
audit events, and the committed manifest result. Schema migration and work-area
creation happen during a separately approved deployment. Snowflake DDL implicitly
commits and READ COMMITTED isolation can expose a different committed source
between statements. Immutable batch scope and serialized publishing establish
the intended consistency. [Snowflake transactions](https://docs.snowflake.com/en/sql-reference/transactions)

Rollback failures must preserve the original error. A lost connection or uncertain
commit requires durable-state inspection. A committed checkpoint is written
atomically with the data, allowing a retry to identify completed publication.
When connectivity still prevents that inspection, keep the attempt outcome
UNKNOWN_COMMIT and stop data writes until the state can be resolved.

## Idempotency and reconciliation

Idempotency is defined at both batch and business-version levels. An identical
successful intent returns its committed result. A new file containing the same
canonical observations records new provenance while making zero current-row
changes. Approved revisions have stable identities tied to exact before/after
values. Older accepted files cannot automatically reverse newer revisions.

For each valid deduplicated batch, unique candidate count equals NEW plus
UNCHANGED plus REVISED. Parsed input data-row count equals unique candidates plus identical
duplicate extras. Conflicting groups are reported separately for rejected batches.

For a successful publication, each current table's after count equals its before
count plus NEW key count. Revisions preserve row count. Every affected key must
match its accepted version across current layers, with one key occurrence per
layer. Compare metric NULL states individually and compare warehouse FLOAT
projections using the approved conversion/comparison policy. Preserve source
Decimal payloads independently for change detection.

Track expected NULL deltas per metric from the approved changes. A new row with
a NULL adds one NULL to that metric. An approved NULL-to-value correction reduces
it by one and a value-to-NULL correction increases it by one. Unaffected periods
retain their previous values. The original global counts remain assertions for
the original fixture and its unchanged historical subset.

The existing calendar, fixed-count Analytics loader, and metadata-dependent
legacy RAW/OPS executor require reviewed adaptation during implementation.
Their current behavior is documented in [the warehouse contract guide](warehouse-contract.md).

## Structured logging and operational evidence

Emit JSON events containing dataset/batch/attempt IDs, file hash, mode, phase,
status, actual UTC time, elapsed duration, and query identifiers when available.
Record source row count, duplicate extras, candidate classifications, inserted/
revised/unchanged counts, per-metric NULL counts, and reconciliation outcomes.
Events cover receipt, capture, validation, review, publication, rollback, retry,
and commit-resolution decisions.

Keep full before/after observations in the audit journal. Logs reference their
IDs and contain bounded diagnostics. Credentials, authentication tokens,
private keys, and connector connection dictionaries are excluded from logs.
Sanitize exception details, preserve error class/code and query ID, and use the
same attempt ID across all events from one invocation. Retain logs durably so a
process crash can be investigated alongside manifest checkpoints.

## Tests required before live-data changes

| Test group | Required cases and acceptance criteria |
| --- | --- |
| Historical fixture | Original file checksum, 66 unique consecutive periods, exact source payloads, seven NULL counts, and canonical aliases match contract 1.1 |
| Input modes | A full snapshot with a synthetic 67th quarter adds one key. A one-quarter delta produces the same current result. Delta scope omissions retain existing periods. An incomplete/stale full snapshot fails completeness checks |
| Classification | Independent NEW, UNCHANGED, REVISED, and CONFLICTING_DUPLICATE cases. Identical duplicates collapse with complete ordinal evidence. Conflicting rows never enter a MERGE source |
| Canonicalization | Thousands separators, whitespace, insignificant fractional zeros, input order, filenames, NULL, and genuine zero. Invalid numeric text and unsupported formats fail validation. Hash version/context is stable and documented |
| FLOAT boundary | Source changes that project to the same FLOAT remain auditable revisions. Warehouse comparisons follow the approved precision rule. NaN, infinities, and negative measurements fail validation |
| NULL preservation | Every original NULL survives unchanged observations. Approved NULL-to-value and value-to-NULL revisions publish and retain before/after audit evidence. Rejected revisions preserve the accepted state |
| Approval | Pending, approved, declined, stale expected-before hash, substituted file, substituted key, and changed policy context. An approval cannot authorize a different revision |
| Replay | Same bytes under another filename, same successful intent, incomplete prior attempt, reordered equivalent file, older accepted file after a newer revision, and replayed approval. Current keys remain unique |
| Atomicity | Inject failures after capture, after each current-layer write, during audit/checkpoint writes, and during reconciliation. Current layers and committed audit/checkpoint state agree after rollback/retry |
| Commit uncertainty | Simulate successful commit with lost acknowledgment and failed commit. Durable-state lookup distinguishes outcomes before any retry writes. Failure logging preserves a previously committed checkpoint |
| Concurrency | Two publishers for one intent and overlapping intents. Approved coordination admits one publisher and stale approvals fail. Direct/manual writer conflicts are detected and investigated |
| Schema and scope | Reordered/renamed/extra/missing columns, changed numeric representation, invalid period keys, missing intervening quarter, and future-period policy. Schema changes require a versioned review |
| Lineage and views | Current fact source relation and latest accepted batch/time are correct. Quarterly uniqueness, partial-year flags, Q4 capacity, and NULL-inclusive coverage match accepted facts |
| Security and recovery | Log redaction, archive/checkpoint integrity, retry limits, crash recovery, and restoration from durable source/audit evidence |

First implement local unit/property tests and in-memory transaction fakes using
clearly labeled synthetic update fixtures. Then, with separate approval, validate
SQL compilation, bindings, FLOAT behavior, constraints, rollback, and MERGE
semantics in isolated Snowflake test objects. Compare full-snapshot and delta
execution against the same expected final state and run each case twice.

Before changing live data, review all deployment/load statements and their
rollback/recovery procedure, pass the approved isolated tests, rehearse baseline
registration, and obtain explicit approval for the exact objects and write scope.
Existing historical test results provide regression evidence for the current
pipeline. Implementation tests for this proposed design remain to be built.

## Decisions requiring approval

| Decision | Recommended choice |
| --- | --- |
| RAW semantics and audit placement | Keep nine-column RAW as current accepted source values, retain immutable history in archived files and the three proposed RAW companion tables |
| Revision publication scope | Hold the entire batch until all revisions are approved, reject its publication if any revision is declined |
| Reviewer and evidence | User approval tied to the file/publication reference and exact before/after hashes, with later delegation explicitly authorized |
| Precision and baseline registration | Canonical Decimal source hashing, proposed four-ULP positive-FLOAT read-back tolerance with exact zero/NULL handling, and a reconciled bootstrap of the historical fixture |
| Existing fact lineage semantics | Existing batch/time fields track the latest accepted materialization, with prior lineage retained in audit history |
| Publication coverage | Declare full snapshot versus delta and expected scope, review gaps, stale snapshots, and implausible future periods against an agreed release policy |

The first implementation review should resolve these choices, choose the FLOAT
projection/comparison rule, and confirm available source publication metadata.
Approval of this design is followed by a separate review of concrete migration
and loading changes. Scheduling is a subsequent phase.
