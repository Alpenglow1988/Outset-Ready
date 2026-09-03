from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from outset_ready.auth import hash_password


def test_root_entrypoint_exports_working_fastapi_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTSET_READY_DB_PATH", str(tmp_path / "ready.sqlite"))
    monkeypatch.setenv("OUTSET_READY_OWNER_EMAIL", "ian@example.com")
    monkeypatch.setenv(
        "OUTSET_READY_OWNER_PASSWORD_HASH",
        hash_password("a-long-test-password"),
    )
    monkeypatch.setenv(
        "OUTSET_READY_SESSION_SECRET",
        "a-test-session-secret-that-is-long-enough",
    )
    entrypoint = Path(__file__).parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("vercel_entrypoint", entrypoint)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert isinstance(module.app, FastAPI)
    with TestClient(module.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
