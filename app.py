"""Vercel-compatible FastAPI entrypoint.

The hosted build is a product preview. Personal SQLite and Garmin data remain in
the local Ready runtime until a managed persistence adapter is introduced.
"""

from outset_ready.deployment import deployment_db_path, is_vercel_deployment
from outset_ready.web import create_app


app = create_app(
    deployment_db_path(),
    preview_mode=is_vercel_deployment(),
)
