# Private Snowflake authentication

**Current sign-in: Google/Microsoft, confirmed by the user.** The local OAuth
path below is the applicable option; the password section is retained only as
an alternative for accounts that already use password/MFA.

## Google/Microsoft: local OAuth

Snowflake's built-in `SNOWFLAKE$LOCAL_APPLICATION` integration supports local
browser sign-in via `OAUTH_AUTHORIZATION_CODE`, if present and enabled. No custom
integration or client secret is needed for that path. See [official local OAuth
instructions](https://docs.snowflake.com/en/user-guide/oauth-local-applications).

ACCOUNTADMIN and other privileged roles are blocked by OAuth defaults. Use an
existing non-privileged role such as SYSADMIN if already granted. The project
rejects privileged OAuth roles rather than changing security policies. See
[OAuth role restrictions](https://docs.snowflake.com/en/user-guide/oauth-snowflake-overview).

```powershell
conda activate telecom_de
$env:SNOWFLAKE_ACCOUNT = Read-Host 'Snowflake organization-account identifier'
$env:SNOWFLAKE_USER = Read-Host 'Snowflake login username'
$env:SNOWFLAKE_WAREHOUSE = 'SNOWFLAKE_LEARNING_WH'
$env:SNOWFLAKE_DATABASE = 'TELECOM_DB'
$env:SNOWFLAKE_ROLE = 'SYSADMIN' # use only if already granted
$env:SNOWFLAKE_AUTHENTICATOR = 'OAUTH_AUTHORIZATION_CODE'
python -m telecom_pipeline.auth_check
```

Sign in with Google/Microsoft in the browser that opens. Do not share passwords,
browser codes, callback URLs, or tokens. PKCE is enabled; token caching is
disabled. The connector uses tokens temporarily in memory to establish the
session. The check omits the database context and performs read-only discovery.
Only the process's connection role changes; no cloud role/grant is modified.
The role and integration availability still require live verification.

If authentication fails, inspect the existing integration from your already
authenticated Snowflake worksheet using read-only SQL:

```sql
SHOW SECURITY INTEGRATIONS LIKE 'SNOWFLAKE$LOCAL_APPLICATION';
```

Check its exact name and ENABLED value. Do not create or alter the integration
or account policies. If SYSADMIN is unavailable, use another already granted
non-privileged role. Share only sanitized error codes or the non-secret integration
name/enabled result. A failed connection does not authorize resource changes.

The externalbrowser attempt failed before SQL execution with 250001 / 08001 and
SAML identity-provider indicators. The account's normal login method determines
the next path; no password or token should be shared in chat.

| Existing sign-in method | Python path |
| --- | --- |
| Snowflake password with existing MFA | Hidden terminal password prompt; MFA remains enforced |
| Organization SAML SSO | externalbrowser with the existing SAML integration |
| Google/Microsoft OIDC | OAuth authorization-code flow with compatible existing account configuration |
| Key pair | Only if the public key is already registered |

Do not create integrations, register keys, reset passwords, change policies, or
disable MFA as part of this task. See [official authentication options](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api)
and [SSO protocol differences](https://docs.snowflake.com/en/user-guide/admin-security-fed-auth-overview).

## Password/MFA, if this is how you already sign in

Run this in **your own terminal**, not in chat or a notebook:

```powershell
conda activate telecom_de
$env:SNOWFLAKE_ACCOUNT = Read-Host 'Snowflake organization-account identifier'
$env:SNOWFLAKE_USER = Read-Host 'Snowflake username'
$env:SNOWFLAKE_ROLE = 'ACCOUNTADMIN'
$env:SNOWFLAKE_WAREHOUSE = 'SNOWFLAKE_LEARNING_WH'
$env:SNOWFLAKE_DATABASE = 'TELECOM_DB'
$env:SNOWFLAKE_AUTHENTICATOR = 'username_password_mfa'
python -m telecom_pipeline.auth_check --prompt-password
```

Only enter the password at the hidden terminal prompt. It is not placed in a
command argument, environment variable, source file, or chat. The module fails
if private non-echoing input is unavailable. Approve your existing MFA flow; if
the account requires a flow the script cannot complete, stop and identify the
supported method rather than weakening security.

The password stays in process memory during authentication; Python cannot
guarantee zeroization of immutable strings. The module does not persist it.
Client token caching is explicitly disabled. No tokens are requested from you
or printed. Errors contain only exception class, code, and SQLSTATE, not the
connector's potentially sensitive exception text. Share only sanitized results.

The connection omits the database context so absent TELECOM_DB does not prevent
authentication. The only SQL is a read-only SHOW DATABASES lookup with exact-name
filtering. An example success response is:

```json
{"status":"AUTHENTICATED","database":"TELECOM_DB","exists":false,"read_only":true}
```

This is an **example**, not an actual observed success. The password/MFA path is
unit-tested but has not been tested against your account. Read-only success does
not authorize any database creation or loading.

## Existing SAML SSO

Use the same non-secret environment settings with
`$env:SNOWFLAKE_AUTHENTICATOR = 'externalbrowser'`, then run:

```powershell
python -m telecom_pipeline.auth_check
```

Do not supply `--prompt-password` for browser authentication. Google/Microsoft
sign-in is not automatically SAML SSO. Local OAuth is now configured above. No
new cloud authentication resources will be created without approval.
