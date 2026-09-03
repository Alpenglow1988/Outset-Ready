"""Vercel-compatible FastAPI entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from outset_ready.settings import load_app_settings
from outset_ready.web import create_app


app = create_app(settings=load_app_settings())
