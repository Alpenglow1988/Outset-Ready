from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def is_vercel_deployment(environment: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environment is None else environment
    return values.get("VERCEL") == "1"


def deployment_db_path(environment: Mapping[str, str] | None = None) -> Path | None:
    values = os.environ if environment is None else environment
    configured_path = values.get("OUTSET_READY_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    if is_vercel_deployment(values):
        return Path("/tmp/outset_ready.sqlite")
    return None
