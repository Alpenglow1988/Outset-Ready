from pathlib import Path

import pytest

from outset_ready.auth import hash_password
from outset_ready.settings import ConfigurationError, load_app_settings


BASE_ENVIRONMENT = {
    "OUTSET_READY_OWNER_EMAIL": "ian@example.com",
    "OUTSET_READY_OWNER_PASSWORD_HASH": hash_password("a-long-test-password"),
    "OUTSET_READY_SESSION_SECRET": "a-test-session-secret-that-is-long-enough",
}


def test_local_settings_use_sqlite_and_insecure_cookie_for_http():
    settings = load_app_settings(BASE_ENVIRONMENT)

    assert settings.database_target == Path("data/outset_ready.sqlite")
    assert not settings.secure_cookies
    assert not settings.persistent_storage


def test_vercel_requires_durable_postgres():
    with pytest.raises(ConfigurationError, match="Temporary SQLite is not accepted"):
        load_app_settings({**BASE_ENVIRONMENT, "VERCEL": "1"})


def test_vercel_accepts_managed_postgres_and_secures_cookie():
    settings = load_app_settings(
        {
            **BASE_ENVIRONMENT,
            "VERCEL": "1",
            "DATABASE_URL": "postgresql://owner:redacted@database.example/ready",
        }
    )

    assert settings.persistent_storage
    assert settings.secure_cookies


@pytest.mark.parametrize(
    "missing_name",
    [
        "OUTSET_READY_OWNER_EMAIL",
        "OUTSET_READY_OWNER_PASSWORD_HASH",
        "OUTSET_READY_SESSION_SECRET",
    ],
)
def test_authentication_settings_are_required(missing_name):
    environment = dict(BASE_ENVIRONMENT)
    del environment[missing_name]

    with pytest.raises(ConfigurationError, match=missing_name):
        load_app_settings(environment)
