import pytest

from telecom_pipeline.auth_check import check, hidden_password


def env():
    return {f"SNOWFLAKE_{key}": value for key, value in {
        "ACCOUNT": "test-account", "USER": "test-user", "ROLE": "ACCOUNTADMIN",
        "WAREHOUSE": "SNOWFLAKE_LEARNING_WH", "DATABASE": "TELECOM_DB",
        "AUTHENTICATOR": "username_password_mfa",
    }.items()}


def test_private_prompt_and_read_only_connection(capsys):
    settings = env()
    captured = {}

    class Cursor:
        description = [("name",)]

        def execute(self, sql, params):
            assert sql == "SHOW DATABASES LIKE %s"

        def fetchall(self):
            return []

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    def connector(**options):
        captured.update(options)
        return Connection()

    report = check(prompt_password=True, environ=settings, connector=connector,
                   prompt=lambda _: "synthetic-private-password")
    assert captured["password"] == "synthetic-private-password"
    assert captured["client_store_temporary_credential"] is False
    assert "database" not in captured
    assert "SNOWFLAKE_PASSWORD" not in settings
    assert report == {"status": "AUTHENTICATED", "database": "TELECOM_DB", "exists": False, "read_only": True}
    assert "synthetic-private-password" not in repr(report)
    assert capsys.readouterr().out == ""


def test_no_prompt_on_wrong_method():
    settings = env()
    settings["SNOWFLAKE_AUTHENTICATOR"] = "externalbrowser"
    with pytest.raises(ValueError):
        check(prompt_password=True, environ=settings, prompt=lambda _: pytest.fail("must not prompt"))


def test_hidden_input_requires_private_terminal(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError):
        hidden_password("Password:")


def test_local_oauth_is_secretless_and_uses_pkce():
    from telecom_pipeline.snowflake_config import connection_options
    settings = env()
    settings.update(SNOWFLAKE_AUTHENTICATOR="OAUTH_AUTHORIZATION_CODE", SNOWFLAKE_ROLE="SYSADMIN")
    options = connection_options(settings, include_database=False)
    assert options["oauth_client_id"] == "LOCAL_APPLICATION"
    assert options["oauth_disable_pkce"] is False
    assert options["client_store_temporary_credential"] is False
    assert "oauth_client_secret" not in options
    assert "password" not in options
    assert "database" not in options


@pytest.mark.parametrize("role", ["ACCOUNTADMIN", "SECURITYADMIN", "ORGADMIN", "GLOBALORGADMIN"])
def test_local_oauth_does_not_bypass_privileged_role_restrictions(role):
    from telecom_pipeline.snowflake_config import connection_options
    settings = env()
    settings.update(SNOWFLAKE_AUTHENTICATOR="OAUTH_AUTHORIZATION_CODE", SNOWFLAKE_ROLE=role)
    with pytest.raises(ValueError, match="non-privileged"):
        connection_options(settings)
