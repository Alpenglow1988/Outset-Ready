"""Vercel-compatible FastAPI entrypoint.

The hosted build is a product preview. Personal SQLite and Garmin data remain in
the local Ready runtime until a managed persistence adapter is introduced.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from outset_ready.deployment import deployment_db_path, is_vercel_deployment
from outset_ready.web import create_app


app = create_app(
    deployment_db_path(),
    preview_mode=is_vercel_deployment(),
)
