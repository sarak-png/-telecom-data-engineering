"""Read-only database discovery. Never creates or modifies Snowflake objects."""
import json
import os
import sys

from .snowflake_config import connect, connection_options, project_database


def database_exists(database, *, connector=None):
    database = project_database(database)
    factory = connector or (lambda: connect(include_database=False))
    connection = factory()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW DATABASES LIKE %s", (database,))
        names = [column[0].lower() for column in cursor.description]
        index = names.index("name")
        # SHOW LIKE treats underscores as wildcards; compare exact identifiers.
        return any(row[index] == database for row in cursor.fetchall())
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.close()


def main():
    try:
        connection_options(include_database=False)
    except ValueError as exc:
        # Configuration errors contain only variable names and fixed validation text.
        print(json.dumps({"status": "CONFIGURATION_REQUIRED", "message": str(exc)}), file=sys.stderr)
        return 2
    try:
        database = project_database(os.environ["SNOWFLAKE_DATABASE"])
        exists = database_exists(database)
        print(json.dumps({"database": database, "exists": exists, "read_only": True}))
        return 0
    except Exception as exc:
        # Do not print connector exception messages, connection args, or tokens.
        text = str(exc).lower()
        categories = [label for term, label in (
            ("identity provider", "identity-provider configuration issue"),
            ("saml", "SAML authentication issue"),
            ("not supported", "requested authentication unsupported"),
            ("404", "endpoint not found"),
        ) if term in text]
        print(json.dumps({"status": "CHECK_FAILED", "error_type": type(exc).__name__,
                          "errno": getattr(exc, "errno", None),
                          "sqlstate": getattr(exc, "sqlstate", None),
                          "categories": categories}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
