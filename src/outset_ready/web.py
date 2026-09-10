from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
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
    import_hosted_garmin_plan,
    save_uploaded_token,
    sync_hosted_garmin,
)
from outset_ready.connectors.garmin.tokens import (
    MAX_TOKEN_BUNDLE_BYTES,
    GarminTokenBundleError,
)
from outset_ready.domain import (
    ActivityType,
    EvidenceKind,
    EvidenceSource,
    GoalCategory,
    GoalPriority,
    InterpretationStatus,
    OPTIONAL_CONTEXT_KINDS,
    PlanChangeReason,
    PlanChangeType,
    PlannedSessionStatus,
    WeeklyReviewStatus,
)
from outset_ready.history import calculate_history_progress
from outset_ready.periods import current_training_week, last_completed_training_week
from outset_ready.plans import (
    auto_match_planned_sessions,
    build_plan_week,
    create_manual_session,
    fetch_planned_session,
    match_planned_session,
    restore_planned_session,
    skip_planned_session,
    unmatch_planned_session,
    update_planned_session,
)
from outset_ready.reviews import (
    PROMPT_VERSION,
    OpenAIWeeklyReviewInterpreter,
    WeeklyReviewInterpreter,
    build_review_snapshot,
    classify_interpretation_error,
    interpretation_failure_message,
    interpretation_failure_retryable,
    load_review_snapshot,
)
from outset_ready.settings import AppSettings, load_app_settings
from outset_ready.session import SignedSessionMiddleware
from outset_ready.storage import (
    ConnectorSyncAlreadyRunning,
    add_manual_evidence,
    archive_goal,
    connect,
    count_evidence_days,
    create_goal,
    database_is_ready,
    claim_weekly_review_interpretation,
    complete_weekly_review_interpretation,
    fetch_connector_connection,
    fetch_latest_connector_sync,
    fetch_owner_data_bounds,
    fetch_weekly_review,
    fetch_weekly_review_interpretation,
    fail_weekly_review_interpretation,
    finalise_weekly_review,
    init_db,
    list_goals_as_of,
    list_connector_syncs,
    list_goals,
    list_recent_activities,
    list_recent_evidence,
    list_weekly_review_interpretations,
    list_weekly_reviews,
    save_weekly_review_draft,
    update_goal,
)
from outset_ready.weekly import build_weekly_read


PACKAGE_DIR = Path(__file__).parent
LOGGER = logging.getLogger(__name__)
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


def create_app(
    *,
    settings: AppSettings | None = None,
    review_interpreter: WeeklyReviewInterpreter | None = None,
) -> FastAPI:
    settings = settings or load_app_settings()
    if review_interpreter is None and settings.openai_api_key:
        review_interpreter = OpenAIWeeklyReviewInterpreter(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(
            settings.database_target,
            owner_email=settings.owner_email,
            user_id=settings.owner_id,
        )
        yield

    app = FastAPI(title="Outset Ready", version="0.7.1", lifespan=lifespan)
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
    templates.env.filters["date_label"] = _date_label

    @app.middleware("http")
    async def private_response_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/health":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
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
            period_goals = list_goals_as_of(
                conn,
                effective_at=_end_of_day(period_end),
                user_id=user_id,
            )
            weekly_read = build_weekly_read(
                conn,
                period_start=period_start,
                period_end=period_end,
                target_weight_kg=_current_weight_target(period_goals),
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

    def render_goals(
        request: Request,
        *,
        notice: str | None = None,
        error: str | None = None,
        status_code: int = 200,
    ):
        user_id = _require_owner(request, settings)
        with connect(settings.database_target) as conn:
            goals = list_goals(conn, user_id=user_id)
            archived_goals = [
                goal
                for goal in list_goals(
                    conn,
                    user_id=user_id,
                    include_archived=True,
                )
                if goal.archived_at is not None
            ]
        return templates.TemplateResponse(
            request=request,
            name="goals.html",
            context={
                "goals": goals,
                "archived_goals": archived_goals,
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
                "persistent_storage": settings.persistent_storage,
                "goal_categories": GoalCategory,
                "goal_priorities": GoalPriority,
                "notice": notice,
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/goals")
    def goals_page(request: Request, notice: str | None = None):
        notices = {
            "created": "Goal added to your active stack.",
            "updated": "Goal changes saved.",
            "archived": "Goal archived. Its earlier history remains intact.",
        }
        return render_goals(request, notice=notices.get(notice or ""))

    @app.post("/goals")
    def create_goal_route(
        request: Request,
        title: str = Form(...),
        category: str = Form(...),
        priority: str = Form(...),
        target_value: str = Form(""),
        target_unit: str = Form(""),
        target_date: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            fields = _parse_goal_fields(
                title=title,
                category=category,
                priority=priority,
                target_value=target_value,
                target_unit=target_unit,
                target_date=target_date,
            )
            with connect(settings.database_target) as conn:
                create_goal(conn, user_id=user_id, **fields)
        except ValueError as exc:
            return render_goals(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/goals?notice=created",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/goals/{goal_id}")
    def update_goal_route(
        request: Request,
        goal_id: str,
        title: str = Form(...),
        category: str = Form(...),
        priority: str = Form(...),
        target_value: str = Form(""),
        target_unit: str = Form(""),
        target_date: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            fields = _parse_goal_fields(
                title=title,
                category=category,
                priority=priority,
                target_value=target_value,
                target_unit=target_unit,
                target_date=target_date,
            )
            with connect(settings.database_target) as conn:
                update_goal(conn, goal_id=goal_id, user_id=user_id, **fields)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_goals(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/goals?notice=updated",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/goals/{goal_id}/archive")
    def archive_goal_route(
        request: Request,
        goal_id: str,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            with connect(settings.database_target) as conn:
                archive_goal(conn, goal_id=goal_id, user_id=user_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_goals(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/goals?notice=archived",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    def render_current_week(
        request: Request,
        *,
        notice: str | None = None,
        error: str | None = None,
        status_code: int = 200,
    ):
        user_id = _require_owner(request, settings)
        today = date.today()
        period_start, period_end = current_training_week(today)
        with connect(settings.database_target) as conn:
            plan = build_plan_week(
                conn,
                period_start=period_start,
                period_end=period_end,
                user_id=user_id,
            )
            garmin_connection = fetch_connector_connection(
                conn,
                connector="garmin",
                user_id=user_id,
            )
        all_activities = {
            (activity.source.value, activity.external_id): activity
            for activity in plan.unmatched_activities
        }
        for row in plan.sessions:
            if row.matched_activity is not None:
                activity = row.matched_activity
                all_activities[(activity.source.value, activity.external_id)] = activity
        days = []
        for offset in range(7):
            day = period_start + timedelta(days=offset)
            days.append(
                {
                    "date": day,
                    "sessions": tuple(
                        row for row in plan.sessions if row.session.scheduled_on == day
                    ),
                    "activities": tuple(
                        sorted(
                            (
                                activity
                                for activity in all_activities.values()
                                if activity.recorded_on == day
                            ),
                            key=lambda activity: (
                                activity.activity_type.value,
                                activity.name or "",
                                activity.external_id,
                            ),
                        )
                    ),
                }
            )
        session_titles = {
            row.session.id: row.session.title for row in plan.sessions
        }
        revision_entries = tuple(
            (revision, session_titles.get(revision.planned_session_id, "Planned session"))
            for revision in plan.revisions
        )
        return templates.TemplateResponse(
            request=request,
            name="week_in_progress.html",
            context={
                "plan": plan,
                "days": days,
                "revision_entries": revision_entries,
                "adjusted_sessions": plan.skipped_sessions
                + sum(
                    row.session.status is PlannedSessionStatus.REMOVED
                    for row in plan.sessions
                ),
                "due_unmatched": plan.due_unmatched_sessions(
                    through_date=min(today - timedelta(days=1), period_end)
                ),
                "garmin_connection": garmin_connection,
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
                "persistent_storage": settings.persistent_storage,
                "activity_types": ActivityType,
                "plan_change_types": (
                    PlanChangeType.EDITED,
                    PlanChangeType.MOVED,
                    PlanChangeType.REPLACED,
                    PlanChangeType.SHORTENED,
                ),
                "plan_change_reasons": PlanChangeReason,
                "today": today,
                "notice": notice,
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/week/current")
    def current_week_page(request: Request, notice: str | None = None):
        notices = {
            "garmin-plan-refreshed": "Garmin Calendar plan refreshed for this week.",
            "session-added": "Planned session added.",
            "session-updated": "Planned session change recorded.",
            "session-skipped": "Session marked as skipped and kept in the week history.",
            "session-restored": "Session restored to the active plan.",
            "activity-matched": "Completed activity matched to the planned session.",
            "activity-unmatched": "Activity match removed.",
        }
        return render_current_week(request, notice=notices.get(notice or ""))

    @app.post("/week/current/garmin")
    def refresh_current_week_garmin(
        request: Request,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        period_start, period_end = current_training_week(date.today())
        try:
            import_hosted_garmin_plan(
                settings,
                user_id=user_id,
                start_date=period_start,
                end_date=period_end,
            )
        except (GarminAuthenticationRequiredError, GarminConnectionUnavailable):
            return render_current_week(
                request,
                error=(
                    "Garmin requires a new token file. Existing plans and manual "
                    "changes remain available."
                ),
                status_code=409,
            )
        except GarminConnectorError:
            return render_current_week(
                request,
                error=(
                    "Garmin Calendar could not be refreshed. The current saved plan "
                    "and manual changes remain available."
                ),
                status_code=502,
            )
        return RedirectResponse(
            url="/week/current?notice=garmin-plan-refreshed",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions")
    def create_current_week_session(
        request: Request,
        scheduled_on: str = Form(...),
        activity_type: str = Form(...),
        title: str = Form(...),
        duration_minutes: str = Form(""),
        distance_km: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            fields = _parse_planned_session_fields(
                scheduled_on=scheduled_on,
                activity_type=activity_type,
                title=title,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
            )
            _require_current_week_date(fields["scheduled_on"])
            with connect(settings.database_target) as conn:
                create_manual_session(conn, user_id=user_id, **fields)
                period_start, period_end = current_training_week(date.today())
                auto_match_planned_sessions(
                    conn,
                    start_date=period_start,
                    end_date=period_end,
                    user_id=user_id,
                )
        except ValueError as exc:
            return render_current_week(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/week/current?notice=session-added",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions/{session_id}")
    def update_current_week_session(
        request: Request,
        session_id: str,
        scheduled_on: str = Form(...),
        activity_type: str = Form(...),
        title: str = Form(...),
        duration_minutes: str = Form(""),
        distance_km: str = Form(""),
        change_type: str = Form(...),
        reason: str = Form(""),
        reason_note: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            fields = _parse_planned_session_fields(
                scheduled_on=scheduled_on,
                activity_type=activity_type,
                title=title,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
            )
            _require_current_week_date(fields["scheduled_on"])
            parsed_change_type = PlanChangeType(change_type)
            with connect(settings.database_target) as conn:
                _require_current_week_session(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                )
                update_planned_session(
                    conn,
                    session_id=session_id,
                    change_type=parsed_change_type,
                    reason=_parse_optional_plan_reason(reason),
                    reason_note=reason_note,
                    user_id=user_id,
                    **fields,
                )
                period_start, period_end = current_training_week(date.today())
                auto_match_planned_sessions(
                    conn,
                    start_date=period_start,
                    end_date=period_end,
                    user_id=user_id,
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_current_week(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/week/current?notice=session-updated",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions/{session_id}/skip")
    def skip_current_week_session(
        request: Request,
        session_id: str,
        reason: str = Form(""),
        reason_note: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            with connect(settings.database_target) as conn:
                _require_current_week_session(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                )
                skip_planned_session(
                    conn,
                    session_id=session_id,
                    reason=_parse_optional_plan_reason(reason),
                    reason_note=reason_note,
                    user_id=user_id,
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_current_week(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/week/current?notice=session-skipped",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions/{session_id}/restore")
    def restore_current_week_session(
        request: Request,
        session_id: str,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            with connect(settings.database_target) as conn:
                _require_current_week_session(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                )
                restore_planned_session(conn, session_id=session_id, user_id=user_id)
                period_start, period_end = current_training_week(date.today())
                auto_match_planned_sessions(
                    conn,
                    start_date=period_start,
                    end_date=period_end,
                    user_id=user_id,
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_current_week(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/week/current?notice=session-restored",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions/{session_id}/match")
    def match_current_week_session(
        request: Request,
        session_id: str,
        activity_identity: str = Form(...),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            source, external_id = _parse_activity_identity(activity_identity)
            with connect(settings.database_target) as conn:
                _require_current_week_session(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                )
                match_planned_session(
                    conn,
                    session_id=session_id,
                    activity_source=source,
                    activity_external_id=external_id,
                    user_id=user_id,
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            return render_current_week(request, error=str(exc), status_code=400)
        return RedirectResponse(
            url="/week/current?notice=activity-matched",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/week/current/sessions/{session_id}/unmatch")
    def unmatch_current_week_session(
        request: Request,
        session_id: str,
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        try:
            with connect(settings.database_target) as conn:
                _require_current_week_session(
                    conn,
                    session_id=session_id,
                    user_id=user_id,
                )
                unmatch_planned_session(conn, session_id=session_id, user_id=user_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RedirectResponse(
            url="/week/current?notice=activity-unmatched",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get("/week")
    def weekly_read_page(
        request: Request,
        review_id: str | None = None,
        notice: str | None = None,
    ):
        user_id = _require_owner(request, settings)
        today = date.today()
        with connect(settings.database_target) as conn:
            _materialise_due_weekly_reviews(conn, today=today, user_id=user_id)
            reviews = list_weekly_reviews(conn, user_id=user_id)
            if not reviews:  # pragma: no cover
                raise HTTPException(status_code=404, detail="No weekly review exists.")
            selected_review = (
                fetch_weekly_review(conn, review_id=review_id, user_id=user_id)
                if review_id
                else reviews[0]
            )
            if selected_review is None:
                raise HTTPException(status_code=404, detail="Weekly review not found.")
            interpretation = fetch_weekly_review_interpretation(
                conn,
                review_id=selected_review.id,
                user_id=user_id,
            )
            interpretations = list_weekly_review_interpretations(
                conn,
                user_id=user_id,
            )
            review_history = [
                {
                    "review": item,
                    "interpretation": interpretations.get(item.id),
                }
                for item in reviews
            ]

        snapshot = load_review_snapshot(selected_review.snapshot_json)
        interpretation_failure = (
            interpretation_failure_message(interpretation.failure_code)
            if interpretation
            and interpretation.status is InterpretationStatus.FAILED
            else None
        )
        interpretation_retryable = (
            interpretation_failure_retryable(interpretation.failure_code)
            if interpretation
            and interpretation.status is InterpretationStatus.FAILED
            else True
        )
        newer_revision_exists = any(
            item.period_start == selected_review.period_start
            and item.revision > selected_review.revision
            for item in reviews
        )
        notices = {
            "finalised": "Weekly review finalised and the interpretation saved.",
            "finalised-without-ai": (
                "Weekly review finalised. AI interpretation is not configured yet."
            ),
            "already-finalised": "This review was already finalised.",
            "evidence-updated": (
                "The evidence changed, so Ready created a new draft for you to check."
            ),
            "interpretation-failed": (
                "The review is finalised, but its interpretation could not be completed. "
                "Your confirmed evidence is safe. See the reason below."
            ),
        }
        return templates.TemplateResponse(
            request=request,
            name="week.html",
            context={
                "weekly_read": snapshot["weekly_read"],
                "review_goals": snapshot["goals"],
                "review": selected_review,
                "interpretation": interpretation,
                "interpretation_failure": interpretation_failure,
                "interpretation_retryable": interpretation_retryable,
                "review_history": review_history,
                "newer_revision_exists": newer_revision_exists,
                "review_status": WeeklyReviewStatus,
                "interpretation_status": InterpretationStatus,
                "ai_available": review_interpreter is not None,
                "notice": notices.get(notice or ""),
                "owner_email": settings.owner_email,
                "csrf_token": _session_csrf_token(request),
                "persistent_storage": settings.persistent_storage,
            },
        )

    @app.post("/week/reviews/{review_id}/confirm")
    def confirm_weekly_review(
        request: Request,
        review_id: str,
        confirmed: str = Form(""),
        csrf_token: str = Form(...),
    ):
        user_id = _require_owner(request, settings)
        _require_csrf(request, csrf_token)
        if confirmed != "yes":
            raise HTTPException(
                status_code=400,
                detail="Confirm that you checked the weekly evidence.",
            )

        with connect(settings.database_target) as conn:
            review = fetch_weekly_review(conn, review_id=review_id, user_id=user_id)
            if review is None:
                raise HTTPException(status_code=404, detail="Weekly review not found.")
            current = _materialise_weekly_review(
                conn,
                period_start=review.period_start,
                period_end=review.period_end,
                user_id=user_id,
            )
            if current.id != review.id:
                return RedirectResponse(
                    url=f"/week?review_id={quote(current.id)}&notice=evidence-updated",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            finalised = finalise_weekly_review(
                conn,
                review_id=review.id,
                expected_fingerprint=review.evidence_fingerprint,
                user_id=user_id,
            )

        if review_interpreter is None:
            return RedirectResponse(
                url=(
                    f"/week?review_id={quote(finalised.id)}"
                    "&notice=finalised-without-ai"
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with connect(settings.database_target) as conn:
            claimed = claim_weekly_review_interpretation(
                conn,
                review_id=finalised.id,
                provider=review_interpreter.provider,
                model=review_interpreter.model,
                prompt_version=PROMPT_VERSION,
                user_id=user_id,
            )
        if not claimed:
            return RedirectResponse(
                url=f"/week?review_id={quote(finalised.id)}&notice=already-finalised",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        try:
            result = review_interpreter.interpret(
                load_review_snapshot(finalised.snapshot_json)
            )
        except Exception as exc:
            failure = classify_interpretation_error(exc)
            LOGGER.exception(
                "Weekly interpretation failed review_id=%s provider=%s "
                "model=%s failure_code=%s exception_type=%s status_code=%s "
                "provider_code=%s request_id=%s",
                finalised.id,
                review_interpreter.provider,
                review_interpreter.model,
                failure.code,
                failure.exception_type,
                failure.status_code,
                failure.provider_code,
                failure.request_id,
            )
            with connect(settings.database_target) as conn:
                fail_weekly_review_interpretation(
                    conn,
                    review_id=finalised.id,
                    failure_code=failure.code,
                    user_id=user_id,
                )
            return RedirectResponse(
                url=(
                    f"/week?review_id={quote(finalised.id)}"
                    "&notice=interpretation-failed"
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        with connect(settings.database_target) as conn:
            complete_weekly_review_interpretation(
                conn,
                review_id=finalised.id,
                what_went_well=result.what_went_well,
                main_risk=result.main_risk,
                one_adjustment=result.one_adjustment,
                encouragement=result.encouragement,
                provider_response_id=result.provider_response_id,
                user_id=user_id,
            )
        return RedirectResponse(
            url=f"/week?review_id={quote(finalised.id)}&notice=finalised",
            status_code=status.HTTP_303_SEE_OTHER,
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
        evidence_date = date.fromisoformat(recorded_on)
        with connect(settings.database_target) as conn:
            add_manual_evidence(
                conn,
                recorded_on=evidence_date,
                kind=EvidenceKind(kind),
                value=parsed_value,
                note=note,
                user_id=user_id,
            )
            _, last_completed_end = last_completed_training_week(date.today())
            if evidence_date <= last_completed_end:
                period_start = evidence_date - timedelta(days=evidence_date.weekday())
                _materialise_weekly_review(
                    conn,
                    period_start=period_start,
                    period_end=period_start + timedelta(days=6),
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
            and goal.target_unit
            and goal.target_unit.casefold() == "kg"
            and goal.target_value is not None
        ):
            return float(goal.target_value)
    return None


def _materialise_due_weekly_reviews(conn, *, today: date, user_id: str) -> None:
    latest_start, _ = last_completed_training_week(today)
    bounds = fetch_owner_data_bounds(conn, user_id=user_id)
    if bounds is None:
        first_start = latest_start
    else:
        earliest = bounds[0]
        first_start = earliest - timedelta(days=earliest.weekday())
        first_start = min(first_start, latest_start)

    earliest_supported = latest_start - timedelta(weeks=51)
    first_start = max(first_start, earliest_supported)
    existing_periods = {
        review.period_start for review in list_weekly_reviews(conn, user_id=user_id)
    }
    period_start = first_start
    while period_start <= latest_start:
        if period_start not in existing_periods or period_start == latest_start:
            period_end = period_start + timedelta(days=6)
            _materialise_weekly_review(
                conn,
                period_start=period_start,
                period_end=period_end,
                user_id=user_id,
            )
        period_start += timedelta(weeks=1)


def _materialise_weekly_review(
    conn,
    *,
    period_start: date,
    period_end: date,
    user_id: str,
):
    goals = list_goals_as_of(
        conn,
        effective_at=_end_of_day(period_end),
        user_id=user_id,
    )
    weekly_read = build_weekly_read(
        conn,
        period_start=period_start,
        period_end=period_end,
        target_weight_kg=_current_weight_target(goals),
        user_id=user_id,
    )
    snapshot = build_review_snapshot(weekly_read, goals)
    return save_weekly_review_draft(
        conn,
        period_start=period_start,
        period_end=period_end,
        evidence_fingerprint=snapshot.fingerprint,
        snapshot_json=snapshot.json,
        user_id=user_id,
    )


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


def _date_label(value, format_string: str) -> str:
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, (date, datetime)):
        return value.strftime(format_string)
    return ""


def _parse_goal_fields(
    *,
    title: str,
    category: str,
    priority: str,
    target_value: str,
    target_unit: str,
    target_date: str,
) -> dict:
    try:
        parsed_category = GoalCategory(category)
    except ValueError as exc:
        raise ValueError("Choose a health, fitness or adventure category.") from exc
    try:
        parsed_priority = GoalPriority(priority)
    except ValueError as exc:
        raise ValueError("Choose a current, supporting or future priority.") from exc
    try:
        parsed_target_value = float(target_value) if target_value.strip() else None
    except ValueError as exc:
        raise ValueError("Enter the target as a number.") from exc
    try:
        parsed_target_date = date.fromisoformat(target_date) if target_date else None
    except ValueError as exc:
        raise ValueError("Enter a valid target date.") from exc
    return {
        "title": title,
        "category": parsed_category,
        "priority": parsed_priority,
        "target_value": parsed_target_value,
        "target_unit": target_unit,
        "target_date": parsed_target_date,
    }


def _parse_planned_session_fields(
    *,
    scheduled_on: str,
    activity_type: str,
    title: str,
    duration_minutes: str,
    distance_km: str,
) -> dict:
    try:
        parsed_date = date.fromisoformat(scheduled_on)
    except ValueError as exc:
        raise ValueError("Enter a valid session date.") from exc
    try:
        parsed_activity_type = ActivityType(activity_type)
    except ValueError as exc:
        raise ValueError("Choose a supported activity type.") from exc
    try:
        duration_seconds = (
            float(duration_minutes) * 60 if duration_minutes.strip() else None
        )
        distance_meters = float(distance_km) * 1000 if distance_km.strip() else None
    except ValueError as exc:
        raise ValueError("Enter duration and distance as numbers.") from exc
    return {
        "scheduled_on": parsed_date,
        "activity_type": parsed_activity_type,
        "title": title,
        "planned_duration_seconds": duration_seconds,
        "planned_distance_meters": distance_meters,
    }


def _parse_optional_plan_reason(value: str) -> PlanChangeReason | None:
    if not value.strip():
        return None
    try:
        return PlanChangeReason(value)
    except ValueError as exc:
        raise ValueError("Choose a recognised change reason.") from exc


def _parse_activity_identity(value: str) -> tuple[EvidenceSource, str]:
    try:
        raw_source, external_id = value.split("|", 1)
        source = EvidenceSource(raw_source)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Choose a completed activity to match.") from exc
    if not external_id:
        raise ValueError("Choose a completed activity to match.")
    return source, external_id


def _require_current_week_date(value: date) -> None:
    period_start, period_end = current_training_week(date.today())
    if not period_start <= value <= period_end:
        raise ValueError("Keep this session inside the current Monday to Sunday week.")


def _require_current_week_session(conn, *, session_id: str, user_id: str):
    session = fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    _require_current_week_date(session.scheduled_on)
    return session
