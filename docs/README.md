# Documentation index

Start with the [project overview](../README.md), the
[warehouse contract](warehouse-contract.md) and the
[publication-readiness assessment](publication-readiness.md). The
[SQL index](sql-index.md) classifies every existing script and its execution scope.

The project implements local ingestion, canonical incremental classification,
SQLite batch control, offline MERGE planning and opt-in Snowflake SELECT
diagnostics. Warehouse loading follows the reviewed manual SQL workflow.
Automatic incremental publication and scheduling remain future work.
Offline planning and precision/validation reports retain `publication_allowed=false`.

## Current implementation and operational guides

| Guide | Purpose and evidence boundary |
| --- | --- |
| [Warehouse contract](warehouse-contract.md) | Confirmed RAW/STAGING/ANALYTICS objects, column mappings and recorded DESCRIBE evidence. View result metadata remains partly unverified. |
| [Local ingestion](phase2-ingestion.md) | CSV/XLSX reading, validation, local persistence and event logging. Its linked Phase 2 test report is archived evidence. |
| [Snowflake authentication](snowflake-authentication.md) | Existing authentication options and private setup. Select the method appropriate to the account and use the root README for the current read-only quickstart. |
| [Read-only incremental comparison](snowflake-readonly-dry-run.md) | Fixed SELECT, snapshot/delta inputs, diagnostic FLOAT comparison and safe error output. |
| [FLOAT conversion validation](float-conversion-validation.md) | Manual TELECOM_READONLY validation command, interpretation and offline test evidence. |
| [FLOAT precision policy](float-precision-policy.md) | Implemented offline assessment and observed source formatting. Metric-grid adoption and tolerance decisions require documented evidence and review. |
| [Local batch registry](local-batch-registry.md) | Durable SQLite state, revision decisions, attempt fencing and local recovery. |
| [Offline MERGE planning](offline-merge-planning.md) | Approval-gated review manifests, source identity checks, before-images and proposed transaction requirements. |
| [Source inspection](tra-source-inspection.md) | Original bilingual CSV layout, columns and source-only RAW table reference. Manual loading steps retain their historical context. |
| [Manual data-quality findings](manual-data-quality-validation.md) | User-reported historical warehouse results and the continuing NULL-preservation policy. |
| [Publication readiness](publication-readiness.md) | Checklist separating implemented offline features, proposed publication components and unverified live behavior. |

Recorded test runs are dated evidence. A historical pass count or authentication
outcome describes its recorded execution context. Follow the root README's
offline test command, which explicitly excludes the live write test.

## Design proposals and roadmap

| Document | Scope |
| --- | --- |
| [Incremental loading design](incremental-loading-design.md) | Business keys, canonical versions, revision holds, audit/control proposals and future transaction/recovery behavior. Some components now have offline implementations documented above. |
| [Automation implementation plan](automation-implementation-plan.md) | Phased deliverables and approval boundaries. Its planned file list includes completed offline work and future work; consult publication readiness for implementation status. |

## Retained documents requiring status review

These documents remain in their existing locations because they combine setup,
design or operational material with historical assumptions. No relocation or
content rewrite was inferred from their filenames.

| Document | Review concern |
| --- | --- |
| [Dedicated environment](telecom-de-environment.md) | Useful environment/VS Code guidance alongside original Phase 1 status, local interpreter paths and older test examples. Use the root README for portable setup and current offline testing. |
| [Local preparation](local-preparation.md) | Dated results and commands for the still-present legacy Decimal transformation. Its database-absence finding and authentication status belong to the original execution context. |
| [Phase 3 Snowflake guide](phase3-snowflake.md) | Legacy RAW/OPS/MART setup and loader instructions, earlier role/authentication choices and recorded database-absence context. Review before treating any command as current guidance. |
| [Analytics Layer proposal](analytics-layer-proposal.md) | Combines an original proposed schema with a later confirmed-schema revision. Retain until its remaining design choices and superseded sections are reviewed. |
| [Analytics SQL review](analytics-sql-review.md) | Review rationale for the preserved snapshot SQL and earlier local results. Current object evidence is in the warehouse contract; future incremental execution remains a separate design. |

## Archived phase verification reports

Only the three dated phase verification reports have moved into `archive/`.
Their narrative, commands, historical findings and test results are preserved.
Relative Markdown destinations were adjusted for the new location.

| Report | Historical scope |
| --- | --- |
| [Phase 1 verification](archive/phase1-verification.md) | Initial CSV investigation, environment discovery and seven-test verification on 2026-11-08. |
| [Phase 2 verification](archive/phase2-verification.md) | Local ingestion/rerun evidence and 29-test verification on 2026-11-08. |
| [Phase 3 verification](archive/phase3-verification.md) | Earlier connector/authentication attempts, legacy loader preparation and local verification on 2026-11-09. |

Archived reports remain linked from the relevant guides and readiness evidence.
Their recorded next steps retain their original context.
