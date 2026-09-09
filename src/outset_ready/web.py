from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from outset_ready.auth import csrf_token_matches, new_csrf_token, verify_password
from outset_ready.connectors.garmin.client import (
    GarminAuthenticationRequiredError,
    GarminConnectorError,
)
from outset_ready.connectors.garmin.hosted import (
    GarminConnectionUnavailable,
    disconnect_hosted_garmin,
    save_uploaded_token,
    sync_hosted_garmin,
)
from outset_ready.connectors.garmin.tokens import (
    MAX_TOKEN_BUNDLE_BYTES,
    GarminTokenBundleError,
)
from outset_ready.domain import EvidenceKind, GoalPriority, OPTIONAL_CONTEXT_KINDS
from outset_ready.history import calculate_history_progress
from outset_ready.periods import last_completed_training_week
from outset_ready.settings import AppSettings, load_app_settings
from outset_ready.session import SignedSessionMiddleware
from outset_ready.storage import (
    ConnectorSyncAlreadyRunning,
    add_manual_evidence,
    connect,
    count_evidence_days,
    database_is_ready,
    fetch_connector_connection,
    fetch_latest_connector_sync,
    init_db,
    list_connector_syncs,
    list_goals,
    list_recent_activities,
    list_recent_evidence,
)
from outset_ready.weekly import build_weekly_read


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

    app = FastAPI(title="Outset Ready", version="0.4.0", lifespan=lifespan)
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
        today = date.today()
        with connect(settings.database_target) as conn:
            goals = list_goals(conn, user_id=user_id)
            evidence = list_recent_evidence(conn, user_id=user_id)
            evidence_days = count_evidence_days(conn, user_id=user_id)
            activities = list_recent_activities(conn, limit=5, user_id=user_id)
            garmin_sync = fetch_latest_connector_sync(conn, "garmin", user_id=user_id)
            garmin_connection = fetch_connector_connection(
                conn,
                connector="garmin",
                user_id=user_id,
            )
            garmin_syncs = list_connector_syncs(
                conn,
                "garmin",
                user_id=user_id,
            )
            period_start, period_end = last_completed_training_week(today)
            weekly_read = build_weekly_read(
                conn,
                period_start=period_start,
                period_end=period_end,
                target_weight_kg=_current_weight_target(goals),
                user_id=user_id,
            )

        assessment = weekly_read.assessment
        history_progress = calculate_history_progress(
            garmin_syncs,
            today=today,
        )
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
                "garmin_connection": garmin_connection,
                "today": today.isoformat(),
                "evidence_options": EVIDENCE_OPTIONS,
                "optional_options": OPTIONAL_OPTIONS,
                "csrf_token": _session_csrf_token(request),
                "owner_email": settings.owner_email,
                "persistent_storage": settings.persistent_storage,
                "weekly_read": weekly_read,
                "history_progress": history_progress,
            },
        )

    @app.get("/week")
    def weekly_read_page(request: Request):
        user_id = _require_owner(request, settings)
        today = date.today()
        period_start, period_end = last_completed_training_week(today)
        with connect(settings.database_target) as conn:
            goals = list_goals(conn, user_id=user_id)
            weekly_read = build_weekly_read(
                conn,
                period_start=period_start,
                period_end=period_end,
                target_weight_kg=_current_weight_target(goals),
                user_id=user_id,
            )
        return templates.TemplateResponse(
            request=request,
            name="week.html",
            context={
                "weekly_read": weekly_read,
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
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

    def render_connections(
        request: Request,
        *,
        notice: str | None = None,
        error: str | None = None,
        status_code: int = 200,
    ):
        user_id = _require_owner(request, settings)
        today = date.today()
        with connect(settings.database_target) as conn:
            garmin_sync = fetch_latest_connector_sync(conn, "garmin", user_id=user_id)
            garmin_connection = fetch_connector_connection(
                conn,
                connector="garmin",
                user_id=user_id,
            )
            garmin_syncs = list_connector_syncs(
                conn,
                "garmin",
                user_id=user_id,
            )
        history_progress = calculate_history_progress(
            garmin_syncs,
            today=today,
        )
        return templates.TemplateResponse(
            request=request,
            name="connections.html",
            context={
                "garmin_sync": garmin_sync,
                "garmin_connection": garmin_connection,
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
                "persistent_storage": settings.persistent_storage,
                "notice": notice,
                "error": error,
                "history_progress": history_progress,
            },
            status_code=status_code,
        )

    @app.get("/connections")
    def connections_page(request: Request, notice: str | None = None):
        messages = {
            "garmin-token-saved": (
                "Garmin token saved. Run the first sync to verify the connection."
            ),
            "garmin-sync-complete": "Garmin sync completed.",
            "garmin-history-batch-complete": (
                "Garmin history batch completed. Continue until the history check is full."
            ),
            "garmin-history-complete": (
                "Garmin history already covers the 42-day comparison window."
            ),
            "garmin-disconnected": (
                "Garmin connection removed. Imported evidence remains available."
            ),
        }
        return render_connections(request, notice=messages.get(notice or ""))

    @app.post("/connections/garmin/token")
    async def upload_garmin_token(
        request: Request,
        token_file: UploadFile = File(...),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        token_payload = await token_file.read(MAX_TOKEN_BUNDLE_BYTES + 1)
        await token_file.close()
        try:
            save_uploaded_token(
                settings,
                user_id=user_id,
                token_payload=token_payload,
            )
        except GarminTokenBundleError as exc:
            return render_connections(
                request,
                error=str(exc),
                status_code=400,
            )
        return RedirectResponse(
            url="/connections?notice=garmin-token-saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/connections/garmin/backfill")
    def backfill_garmin_connection(
        request: Request,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        today = date.today()
        with connect(settings.database_target) as conn:
            progress = calculate_history_progress(
                list_connector_syncs(conn, "garmin", user_id=user_id),
                today=today,
            )
        if progress.complete or progress.next_batch is None:
            return RedirectResponse(
                url="/connections?notice=garmin-history-complete",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        try:
            sync_hosted_garmin(
                settings,
                user_id=user_id,
                days=progress.next_batch.days,
                end_date=progress.next_batch.end_date,
            )
        except ConnectorSyncAlreadyRunning:
            return render_connections(
                request,
                error="A Garmin sync is already running. Wait for it to finish.",
                status_code=409,
            )
        except (GarminAuthenticationRequiredError, GarminConnectionUnavailable):
            return render_connections(
                request,
                error=(
                    "Garmin requires a new token file. Authenticate on your Mac, "
                    "then replace the saved connection."
                ),
                status_code=409,
            )
        except GarminConnectorError:
            return render_connections(
                request,
                error=(
                    "Garmin could not finish this history batch. Completed batches "
                    "remain available, so you can retry it."
                ),
                status_code=502,
            )
        return RedirectResponse(
            url="/connections?notice=garmin-history-batch-complete",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/connections/garmin/sync")
    def sync_garmin_connection(
        request: Request,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            sync_hosted_garmin(settings, user_id=user_id)
        except ConnectorSyncAlreadyRunning:
            return render_connections(
                request,
                error="A Garmin sync is already running. Wait for it to finish.",
                status_code=409,
            )
        except (GarminAuthenticationRequiredError, GarminConnectionUnavailable):
            return render_connections(
                request,
                error=(
                    "Garmin requires a new token file. Authenticate on your Mac, "
                    "then replace the saved connection."
                ),
                status_code=409,
            )
        except GarminConnectorError:
            return render_connections(
                request,
                error=(
                    "Garmin could not finish the sync. Your saved connection and "
                    "existing evidence remain available."
                ),
                status_code=502,
            )
        return RedirectResponse(
            url="/connections?notice=garmin-sync-complete",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/connections/garmin/disconnect")
    def disconnect_garmin_connection(
        request: Request,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        disconnect_hosted_garmin(settings, user_id=user_id)
        return RedirectResponse(
            url="/connections?notice=garmin-disconnected",
            status_code=status.HTTP_303_SEE_OTHER,
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


def _current_weight_target(goals) -> float | None:
    for goal in goals:
        if (
            goal.priority is GoalPriority.CURRENT
            and goal.target_unit == "kg"
            and goal.target_value is not None
        ):
            return float(goal.target_value)
    return None
