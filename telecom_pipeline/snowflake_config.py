"""Environment-only connection settings. Never print connection arguments."""
import json
import os
import re
from pathlib import Path


WAREHOUSE_CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "warehouse_contract.json").read_text(encoding="utf-8")
)
DEFAULT_DATABASE = WAREHOUSE_CONTRACT["database"]


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Snowflake identifiers must contain only letters, digits, underscores")
    return value.upper()


def project_database(value):
    name = identifier(value)
    if name == "CUSTOMERS":
        raise ValueError("CUSTOMERS is protected and cannot be a project write target")
    return name


def connection_options(environ=None, *, include_database=True, password_prompt=None):
    env = os.environ if environ is None else environ
    required = ("ACCOUNT", "USER", "ROLE", "WAREHOUSE", "DATABASE", "AUTHENTICATOR")
    missing = [f"SNOWFLAKE_{key}" for key in required if not env.get(f"SNOWFLAKE_{key}")]
    if missing:
        raise ValueError("Missing environment variables: " + ", ".join(missing))
    options = {key.lower(): env[f"SNOWFLAKE_{key}"] for key in required}
    for key in ("database", "role", "warehouse"):
        options[key] = identifier(options[key])
    options["database"] = project_database(options["database"])
    if not include_database:
        # Discovery must authenticate successfully even if the database is absent.
        options.pop("database")
    auth = options["authenticator"]
    if auth in ("snowflake", "username_password_mfa"):
        password = env.get("SNOWFLAKE_PASSWORD")
        if password_prompt is not None:
            # Interactive mode keeps the secret out of environment variables too.
            password = password_prompt("Snowflake password (hidden): ")
        if not password:
            raise ValueError("Password authentication requires a local prompt or SNOWFLAKE_PASSWORD")
        options["password"] = password
    elif auth == "SNOWFLAKE_JWT":
        if not env.get("SNOWFLAKE_PRIVATE_KEY_FILE"):
            raise ValueError("SNOWFLAKE_PRIVATE_KEY_FILE is required for key-pair authentication")
        options["private_key_file"] = env["SNOWFLAKE_PRIVATE_KEY_FILE"]
        if env.get("SNOWFLAKE_PRIVATE_KEY_PASSWORD"):
            options["private_key_password"] = env["SNOWFLAKE_PRIVATE_KEY_PASSWORD"]
    elif auth == "OAUTH_AUTHORIZATION_CODE":
        if options["role"] in {"ACCOUNTADMIN", "SECURITYADMIN", "ORGADMIN", "GLOBALORGADMIN"}:
            raise ValueError("Local OAuth requires an existing non-privileged role; account policies will not be weakened")
        # Use Snowflake's built-in local application; no custom integration or secret.
        options.update(oauth_client_id="LOCAL_APPLICATION", oauth_disable_pkce=False,
                       authentication_timeout=180)
    elif auth != "externalbrowser":
        raise ValueError("Supported authenticators: externalbrowser, SNOWFLAKE_JWT, snowflake, username_password_mfa, OAUTH_AUTHORIZATION_CODE")
    options.update(autocommit=True, login_timeout=60, query_timeout=120,
                   client_store_temporary_credential=False,
                   enable_server_session_keep_alive_auto_detection=False,
                   application="TELECOM_DE_PORTFOLIO",
                   session_parameters={"TIMEZONE": "UTC", "QUERY_TAG": "telecom_de_phase3"})
    if auth == "OAUTH_AUTHORIZATION_CODE":
        options["login_timeout"] = 180
    return options


def connect(*, include_database=True):
    options = connection_options(include_database=include_database)
    import snowflake.connector

    return snowflake.connector.connect(**options)
