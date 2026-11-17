"""User-run, read-only authentication check with optional hidden terminal input."""
import argparse
import getpass
import json
import os
import sys
import warnings

from .snowflake_check import database_exists
from .snowflake_config import connection_options, project_database


def hidden_password(prompt):
    if not sys.stdin.isatty():
        raise RuntimeError("A private interactive terminal is required for password entry")
    # Fail rather than silently falling back to echoed password input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def check(*, prompt_password=False, environ=None, connector=None, prompt=hidden_password):
    env = dict(os.environ if environ is None else environ)
    if prompt_password and env.get("SNOWFLAKE_AUTHENTICATOR") not in ("snowflake", "username_password_mfa"):
        raise ValueError("Use password prompting only with a configured password authentication method")
    options = connection_options(env, include_database=False,
                                 password_prompt=prompt if prompt_password else None)
    database = project_database(env["SNOWFLAKE_DATABASE"])
    if connector is None:
        import snowflake.connector
        connector = snowflake.connector.connect
    try:
        exists = database_exists(database, connector=lambda: connector(**options))
        return {"status": "AUTHENTICATED", "database": database, "exists": exists, "read_only": True}
    finally:
        options.pop("password", None)
        # Python cannot guarantee zeroization of immutable strings in memory.
        # No credentials or tokens are written to disk or serialized by this code.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-password", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(check(prompt_password=args.prompt_password)))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "AUTH_CHECK_FAILED", "error_type": type(exc).__name__,
                          "errno": getattr(exc, "errno", None),
                          "sqlstate": getattr(exc, "sqlstate", None)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
