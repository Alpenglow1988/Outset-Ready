from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from outset_ready.domain import EvidenceKind, OPTIONAL_CONTEXT_KINDS
from outset_ready.readiness import ReadinessSignals, assess_readiness
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    count_evidence_days,
    init_db,
    list_goals,
    list_recent_evidence,
)


PACKAGE_DIR = Path(__file__).parent
EVIDENCE_OPTIONS = (
    (EvidenceKind.WEIGHT_KG, "Weight"),
    (EvidenceKind.WAIST_CM, "Waist"),
    (EvidenceKind.ACTIVITY_MINUTES, "Activity time"),
    (EvidenceKind.SLEEP_HOURS, "Sleep"),
    (EvidenceKind.NOTE, "Context note"),
)
OPTIONAL_OPTIONS = (
    (EvidenceKind.ALCOHOL_UNITS, "Alcohol"),
    (EvidenceKind.CALORIES, "Calories"),
    (EvidenceKind.PROTEIN_G, "Protein"),
)


def create_app(db_path: Path | None = None) -> FastAPI:
    resolved_db_path = db_path or Path(
        os.getenv("OUTSET_READY_DB_PATH", "data/outset_ready.sqlite")
    )
    init_db(resolved_db_path)

    app = FastAPI(title="Outset Ready", version="0.1.0")
    app.state.db_path = resolved_db_path
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.get("/")
    def dashboard(request: Request):
        with connect(app.state.db_path) as conn:
            goals = list_goals(conn)
            evidence = list_recent_evidence(conn)
            evidence_days = count_evidence_days(conn)

        assessment = assess_readiness(ReadinessSignals(evidence_days=evidence_days))
        visible_evidence = [
            record for record in evidence if record.kind not in OPTIONAL_CONTEXT_KINDS
        ]
        optional_evidence = [
            record for record in evidence if record.kind in OPTIONAL_CONTEXT_KINDS
        ]
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "assessment": assessment,
                "goals": goals,
                "evidence": visible_evidence,
                "optional_evidence": optional_evidence,
                "evidence_days": evidence_days,
                "today": date.today().isoformat(),
                "evidence_options": EVIDENCE_OPTIONS,
                "optional_options": OPTIONAL_OPTIONS,
            },
        )

    @app.post("/evidence")
    def create_evidence(
        recorded_on: str = Form(...),
        kind: str = Form(...),
        value: str = Form(""),
        note: str = Form(""),
    ):
        parsed_value = float(value) if value.strip() else None
        with connect(app.state.db_path) as conn:
            add_manual_evidence(
                conn,
                recorded_on=date.fromisoformat(recorded_on),
                kind=EvidenceKind(kind),
                value=parsed_value,
                note=note,
            )
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/goals")
    def goals_api():
        with connect(app.state.db_path) as conn:
            return list_goals(conn)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
