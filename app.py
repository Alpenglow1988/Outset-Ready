"""FastAPI entrypoint for Vercel.

Vercel imports a root-level ``app`` object. The project uses a ``src`` layout,
so add that directory before importing the application package. The hosted app
uses Vercel's writable temporary directory because local SQLite data must never
be deployed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from outset_ready.web import create_app


app = create_app(
    Path(os.getenv("OUTSET_READY_DB_PATH", "/tmp/outset_ready.sqlite"))
)
