from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from outset_ready.auth import csrf_token_matches, new_csrf_token, verify_password
from outset_ready.domain import EvidenceKind, OPTIONAL_CONTEXT_KINDS
from outset_ready.readiness import ReadinessSignals, assess_readiness
from outset_ready.settings import AppSettings, load_app_settings
from outset_ready.session import SignedSessionMiddleware
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    count_evidence_days,
    database_is_ready,
    fetch_latest_connector_sync,
    init_db,
    list_goals,
    list_recent_activities,
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


def create_app(*, settings: AppSettings | None = None) -> FastAPI:
    settings = settings or load_app_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(
            settings.database_target,
            owner_email=settings.owner_email,
            user_id=settings.owner_id,
        )
        yield

    app = FastAPI(title="Outset Ready", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        SignedSessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="outset_ready_session",
        max_age=60 * 60 * 12,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.middleware("http")
    async def private_response_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/health":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.get("/login")
    def login_page(request: Request, next: str = "/"):
        if _owner_id(request, settings):
            return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        return _login_response(
            request,
            templates,
            next_path=_safe_next_path(next),
        )

    @app.post("/login")
    def login(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        next: str = Form("/"),
    ):
        if not csrf_token_matches(request.session.get("csrf_token"), csrf_token):
            raise HTTPException(status_code=403, detail="Invalid form token.")
        valid_identity = email.strip().casefold() == settings.owner_email
        valid_password = verify_password(password, settings.owner_password_hash)
        if not (valid_identity and valid_password):
            return _login_response(
                request,
                templates,
                next_path=_safe_next_path(next),
                error="That email and password combination was not recognised.",
                status_code=401,
            )

        request.session.clear()
        request.session["user_id"] = settings.owner_id
        request.session["csrf_token"] = new_csrf_token()
        return RedirectResponse(
            url=_safe_next_path(next),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form(...)):
        _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        request.session.clear()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/")
    def dashboard(request: Request):
        user_id = _require_owner(request, settings)
        with connect(settings.database_target) as conn:
            goals = list_goals(conn, user_id=user_id)
            evidence = list_recent_evidence(conn, user_id=user_id)
            evidence_days = count_evidence_days(conn, user_id=user_id)
            activities = list_recent_activities(conn, limit=5, user_id=user_id)
            garmin_sync = fetch_latest_connector_sync(conn, "garmin", user_id=user_id)

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
                "activities": activities,
                "garmin_sync": garmin_sync,
                "today": date.today().isoformat(),
                "evidence_options": EVIDENCE_OPTIONS,
                "optional_options": OPTIONAL_OPTIONS,
                "csrf_token": _session_csrf_token(request),
                "owner_email": settings.owner_email,
                "persistent_storage": settings.persistent_storage,
            },
        )

    @app.post("/evidence")
    def create_evidence(
        request: Request,
        recorded_on: str = Form(...),
        kind: str = Form(...),
        value: str = Form(""),
        note: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        parsed_value = float(value) if value.strip() else None
        with connect(settings.database_target) as conn:
            add_manual_evidence(
                conn,
                recorded_on=date.fromisoformat(recorded_on),
                kind=EvidenceKind(kind),
                value=parsed_value,
                note=note,
                user_id=user_id,
            )
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/connections")
    def connections_page(request: Request):
        user_id = _require_owner(request, settings)
        with connect(settings.database_target) as conn:
            garmin_sync = fetch_latest_connector_sync(conn, "garmin", user_id=user_id)
        return templates.TemplateResponse(
            request=request,
            name="connections.html",
            context={
                "garmin_sync": garmin_sync,
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
                "persistent_storage": settings.persistent_storage,
            },
        )

    @app.get("/api/goals")
    def goals_api(request: Request):
        user_id = _require_owner(request, settings, api=True)
        with connect(settings.database_target) as conn:
            return list_goals(conn, user_id=user_id)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness_health():
        if not database_is_ready(settings.database_target):
            raise HTTPException(status_code=503, detail="Database unavailable.")
        return {"status": "ready", "database": "available"}

    return app


def _owner_id(request: Request, settings: AppSettings) -> str | None:
    user_id = request.session.get("user_id")
    return user_id if user_id == settings.owner_id else None


def _require_owner(
    request: Request,
    settings: AppSettings,
    *,
    api: bool = False,
) -> str:
    user_id = _owner_id(request, settings)
    if user_id:
        return user_id
    if api:
        raise HTTPException(status_code=401, detail="Authentication required.")
    next_path = quote(request.url.path, safe="/")
    raise HTTPException(
        status_code=303,
        headers={"Location": f"/login?next={next_path}"},
    )


def _session_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = new_csrf_token()
        request.session["csrf_token"] = token
    return token


def _require_csrf(request: Request, supplied: str) -> None:
    if not csrf_token_matches(request.session.get("csrf_token"), supplied):
        raise HTTPException(status_code=403, detail="Invalid form token.")


def _safe_next_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _login_response(
    request: Request,
    templates: Jinja2Templates,
    *,
    next_path: str,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "csrf_token": _session_csrf_token(request),
            "next": next_path,
            "error": error,
        },
        status_code=status_code,
    )
