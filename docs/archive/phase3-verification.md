# Phase 3 verification — 2026-11-09

**Status: local implementation verified; live Snowflake integration pending.**

## Google/Microsoft authentication follow-up

The user confirmed Google/Microsoft sign-in. Local OAuth authorization-code
support was added using the built-in LOCAL_APPLICATION client, PKCE, disabled
token caching, and an existing non-privileged role. No custom integration,
role/grant, or account-policy changes were performed. ACCOUNTADMIN is explicitly
rejected for this flow rather than weakening Snowflake's privileged-role policy.

An actual read-only OAuth attempt using SYSADMIN timed out waiting for the browser
flow: **OperationalError 14 / HYT00**. Authentication success and role availability
remain unverified. No database lookup result was obtained and no SQL writes were
executed. The user can retry the documented command from their own interactive
terminal. No passwords, callback codes, or tokens were requested in chat or
written into project files.

Latest offline suite after OAuth changes: **72 passed, 1 live test skipped in
1.85s**. See [the authentication guide](../snowflake-authentication.md).

## Earlier verification history

Requested target: TELECOM_DB, existing SNOWFLAKE_LEARNING_WH warehouse,
ACCOUNTADMIN role, externalbrowser authentication. The supplied account identifier
and username were used in process-only environment variables for read-only
connection attempts. Authentication failed before lookup with error **250001**,
SQLSTATE **08001**, and sanitized SAML/identity-provider indicators. The database's
existence was unknown to the connector. The user has since manually checked and
reported TELECOM_DB absent; that finding has not been verified by Python. No cloud SQL has been executed and CUSTOMERS
has not been modified. Browser SSO remains unresolved; no credentials were added
to project files. Client token caching is now explicitly disabled in configuration.

## Actual local results

- Python 3.11.18 in `telecom_de`; official Snowflake connector 5.2.0 installed
  after approval. No packages were installed in base or omantel.
- Local preparation extension: **67 passed, 1 skipped in 1.84s**; see
  [local preparation results](../local-preparation.md).
- The skipped test is the deliberately opt-in live Snowflake load/rerun test.
- `python -m pip check`: **No broken requirements found.**
- Offline loader plan: **66 expected RAW rows**, 9 source columns, 84 missing
  metric cells, 0 duplicate input records, `cloud_executed: false`.
- Initial offline checker: **CONFIGURATION_REQUIRED**. After connection details
  were supplied, the actual read-only attempt failed at authentication as above.
- Raw dataset SHA-256 still matches Phase 1:
  `12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.

Offline tests include all previous ingestion tests plus configuration and auth
branches, identifier validation, exact database-name lookup, CUSTOMERS guards,
source-cell preservation, verification failure cases, approval enforcement,
mocked loading/reruns/rollback, sanitized logs, and local writer exclusion.
The mock is not a Snowflake SQL engine. Actual DDL/MERGE compatibility, SSO,
privileges, row counts in Snowflake, and live transaction behavior remain untested.

## Prepared files

- `.env.example`: non-secret environment-variable reference for the requested target.
- `requirements-snowflake.txt`: optional official connector dependency.
- `snowflake_config.py`: secure environment-only configuration and target guards.
- `snowflake_check.py`: exact-name read-only database discovery without database context.
- `snowflake_loader.py`: offline planning, approved insert-only RAW MERGE, exact
  read-back verification, OPS history, rollback, and connection cleanup.
- `sql/000_create_telecom_db.sql`: database creation only, pending existence check/approval.
- `sql/001_bootstrap.sql`: non-replacing database/schema/format/stage/table definitions.
- `sql/002_merge_raw.sql`: bound JSON source projected to the exact TRA RAW columns.
- `sql/003_monitoring.sql`: read-only monitoring queries.
- `tests/test_snowflake.py`: offline tests; `tests/test_snowflake_live.py`: opt-in test.
- `.gitignore`, README.md, and Phase 3 documentation updated.

## Required next steps

1. Resolve the browser SSO failure without sharing/storing passwords or tokens.
2. Complete the read-only exact-name existence check using working authentication
   or the user's existing authenticated Snowflake worksheet.
3. If absent, request approval for `CREATE DATABASE IF NOT EXISTS TELECOM_DB`.
4. Obtain approval for remaining setup SQL and RAW/OPS loading before execution.
5. Run the actual load twice and enable the live test only after approval.
6. Record cloud results before calling Phase 3 complete. Do not begin Phase 4.

See [setup and execution](../phase3-snowflake.md) for complete commands and limits.
