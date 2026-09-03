from pathlib import Path

from outset_ready.deployment import deployment_db_path, is_vercel_deployment


def test_vercel_uses_temporary_sqlite_path():
    environment = {"VERCEL": "1"}

    assert is_vercel_deployment(environment)
    assert deployment_db_path(environment) == Path("/tmp/outset_ready.sqlite")


def test_explicit_database_path_takes_precedence():
    environment = {
        "VERCEL": "1",
        "OUTSET_READY_DB_PATH": "/tmp/custom-ready.sqlite",
    }

    assert deployment_db_path(environment) == Path("/tmp/custom-ready.sqlite")


def test_local_factory_can_keep_its_default_path():
    assert not is_vercel_deployment({})
    assert deployment_db_path({}) is None
