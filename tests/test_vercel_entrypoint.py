from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_root_entrypoint_exports_working_fastapi_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTSET_READY_DB_PATH", str(tmp_path / "ready.sqlite"))
    entrypoint = Path(__file__).parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("vercel_entrypoint", entrypoint)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert isinstance(module.app, FastAPI)
    assert TestClient(module.app).get("/health").json() == {"status": "ok"}
