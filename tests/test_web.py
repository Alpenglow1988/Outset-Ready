import logging
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from outset_ready.auth import hash_password
from outset_ready.connectors.garmin.client import GarminAuthenticationRequiredError
from outset_ready.credentials import CredentialCipher, generate_credential_encryption_key
from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    ConnectorConnectionStatus,
    ConnectorSyncStatus,
    DailyObservation,
    EvidenceKind,
    EvidenceSource,
    GoalPriority,
    PlanChangeType,
    PlannedSessionStatus,
)
from outset_ready.periods import current_training_week, last_completed_training_week
from outset_ready.plans import (
    create_manual_session,
    list_plan_revisions,
    list_planned_sessions,
)
from outset_ready.reviews import GeneratedInterpretation
from outset_ready.settings import AppSettings
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    fetch_connector_connection,
    finish_connector_sync,
    list_goals,
    list_recent_evidence,
    list_weekly_reviews,
    load_connector_credentials,
    save_connector_credentials,
    start_connector_sync,
    upsert_activity,
    upsert_daily_observation,
)
from outset_ready.web import create_app


OWNER_EMAIL = "ian@example.com"
OWNER_PASSWORD = "a-long-test-password"
OWNER_PASSWORD_HASH = hash_password(OWNER_PASSWORD)
ENCRYPTION_KEY = generate_credential_encryption_key()
VALID_TOKEN = (
    '{"di_token":"access","di_refresh_token":"refresh",'
    '"di_client_id":"client"}'
)


class FakeReviewInterpreter:
    provider = "test"
    model = "test-model"

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.snapshots = []

    def interpret(self, snapshot):
        self.snapshots.append(snapshot)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return GeneratedInterpretation(
            what_went_well="The completed work supported the current direction.",
            main_risk="Recovery evidence needs another week.",
            one_adjustment="Keep the next week unchanged.",
            encouragement="Use the next review to confirm the pattern.",
            provider_response_id="response-test",
        )


@pytest.fixture
def settings(tmp_path):
    return AppSettings(
        database_target=tmp_path / "ready.sqlite",
        owner_email=OWNER_EMAIL,
        owner_password_hash=OWNER_PASSWORD_HASH,
        session_secret="test-session-secret-that-is-long-enough",
        credential_encryption_key=ENCRYPTION_KEY,
        secure_cookies=False,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def sign_in(client: TestClient, *, next_path: str = "/"):
    login_page = client.get(f"/login?next={next_path}")
    return client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "csrf_token": csrf_from(login_page),
            "next": next_path,
        },
        follow_redirects=False,
    )


def test_dashboard_requires_owner_login(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"
    login_page = client.get(response.headers["location"])
    assert login_page.status_code == 200
    assert "Private owner access" in login_page.text


def test_owner_can_sign_in_and_see_reference_goal_stack(client):
    response = sign_in(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Building a picture" in dashboard.text
    assert "Reach 85 kg" in dashboard.text
    assert "Ultra Mirage El Djerid 50 km" in dashboard.text
    assert "never require them" in dashboard.text
    assert OWNER_EMAIL in dashboard.text
    assert client.get("/static/outset-mark.svg").status_code == 200


def test_weekly_read_is_private_and_shows_completed_week_evidence(client, settings):
    assert client.get("/week", follow_redirects=False).status_code == 303
    sign_in(client)
    period_start, period_end = last_completed_training_week(date.today())
    with connect(settings.database_target) as conn:
        for offset in range(7):
            upsert_daily_observation(
                conn,
                DailyObservation(
                    recorded_on=period_start + timedelta(days=offset),
                    source=EvidenceSource.GARMIN,
                    weight_kg=91.6 - (offset * 0.1),
                    sleep_hours=7,
                ),
            )
            upsert_daily_observation(
                conn,
                DailyObservation(
                    recorded_on=period_start - timedelta(days=7 - offset),
                    source=EvidenceSource.GARMIN,
                    weight_kg=92,
                ),
            )
        upsert_activity(
            conn,
            ActivityRecord(
                source=EvidenceSource.GARMIN,
                external_id="weekly-run",
                recorded_on=period_end,
                activity_type=ActivityType.RUN,
                name="Long easy run",
                duration_seconds=5400,
                distance_meters=12000,
            ),
        )

    response = client.get("/week")

    assert response.status_code == 200
    assert "Draft review" in response.text
    assert "Progressing" in response.text
    assert "Long easy run" in response.text
    assert "12.0 km" in response.text
    assert "A missing value stays unknown" in response.text


def test_owner_confirms_one_review_and_reuses_cached_interpretation(settings):
    interpreter = FakeReviewInterpreter()
    with TestClient(
        create_app(settings=settings, review_interpreter=interpreter)
    ) as review_client:
        sign_in(review_client)
        draft = review_client.get("/week")
        reviews = None
        with connect(settings.database_target) as conn:
            reviews = list_weekly_reviews(conn)
        review = reviews[0]

        missing_confirmation = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(draft)},
        )
        assert missing_confirmation.status_code == 400
        assert interpreter.snapshots == []

        finalised = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(draft), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert finalised.status_code == 303
        assert "notice=finalised" in finalised.headers["location"]
        assert len(interpreter.snapshots) == 1

        result_page = review_client.get(finalised.headers["location"])
        assert "Confirmed interpretation" in result_page.text
        assert "The completed work supported the current direction." in result_page.text

        repeated = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(result_page), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert repeated.status_code == 303
        assert "notice=already-finalised" in repeated.headers["location"]
        assert len(interpreter.snapshots) == 1


def test_owner_can_finalise_a_rules_based_review_without_ai(settings):
    with TestClient(create_app(settings=settings)) as review_client:
        sign_in(review_client)
        draft = review_client.get("/week")
        with connect(settings.database_target) as conn:
            review = list_weekly_reviews(conn)[0]

        response = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(draft), "confirmed": "yes"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "notice=finalised-without-ai" in response.headers["location"]
        with connect(settings.database_target) as conn:
            saved = list_weekly_reviews(conn)[0]
        assert saved.status.value == "finalised"


def test_changed_evidence_reopens_a_finalised_week_as_a_visible_revision(settings):
    interpreter = FakeReviewInterpreter()
    with TestClient(
        create_app(settings=settings, review_interpreter=interpreter)
    ) as review_client:
        sign_in(review_client)
        draft = review_client.get("/week")
        with connect(settings.database_target) as conn:
            first = list_weekly_reviews(conn)[0]
        review_client.post(
            f"/week/reviews/{first.id}/confirm",
            data={"csrf_token": csrf_from(draft), "confirmed": "yes"},
        )

        with connect(settings.database_target) as conn:
            add_manual_evidence(
                conn,
                recorded_on=first.period_end,
                kind=EvidenceKind.NOTE,
                note="Travel changed the available training time.",
            )

        revised_page = review_client.get("/week")
        with connect(settings.database_target) as conn:
            reviews = list_weekly_reviews(conn)

        assert reviews[0].revision == 2
        assert reviews[0].status.value == "draft"
        assert reviews[1].status.value == "finalised"
        assert "Revision 2" in revised_page.text
        assert "Travel changed the available training time." in revised_page.text
        assert "Revision 1" in revised_page.text


def test_confirmation_stops_when_evidence_changed_after_page_load(settings):
    interpreter = FakeReviewInterpreter()
    with TestClient(
        create_app(settings=settings, review_interpreter=interpreter)
    ) as review_client:
        sign_in(review_client)
        draft = review_client.get("/week")
        with connect(settings.database_target) as conn:
            first = list_weekly_reviews(conn)[0]
            add_manual_evidence(
                conn,
                recorded_on=first.period_end,
                kind=EvidenceKind.NOTE,
                note="Evidence added after the draft opened.",
            )

        response = review_client.post(
            f"/week/reviews/{first.id}/confirm",
            data={"csrf_token": csrf_from(draft), "confirmed": "yes"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "notice=evidence-updated" in response.headers["location"]
        assert interpreter.snapshots == []
        with connect(settings.database_target) as conn:
            reviews = list_weekly_reviews(conn)
        assert reviews[0].revision == 2
        assert reviews[0].status.value == "draft"


def test_failed_interpretation_is_logged_and_keeps_finalised_review(
    settings,
    caplog,
):
    interpreter = FakeReviewInterpreter(fail=True)
    caplog.set_level(logging.ERROR, logger="outset_ready.web")
    with TestClient(
        create_app(settings=settings, review_interpreter=interpreter)
    ) as review_client:
        sign_in(review_client)
        draft = review_client.get("/week")
        with connect(settings.database_target) as conn:
            review = list_weekly_reviews(conn)[0]

        failed = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(draft), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert failed.status_code == 303
        assert "notice=interpretation-failed" in failed.headers["location"]
        failed_page = review_client.get(failed.headers["location"])
        assert "Your confirmed evidence remains saved" in failed_page.text
        assert "unexpected interpretation error" in failed_page.text
        assert "Retry after fixing OpenAI setup" in failed_page.text

        log_text = caplog.text
        assert "Weekly interpretation failed" in log_text
        assert f"review_id={review.id}" in log_text
        assert "failure_code=unexpected_error" in log_text
        assert "exception_type=RuntimeError" in log_text

        interpreter.fail = False
        retried = review_client.post(
            f"/week/reviews/{review.id}/confirm",
            data={"csrf_token": csrf_from(failed_page), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert retried.status_code == 303
        assert "notice=finalised" in retried.headers["location"]
        assert len(interpreter.snapshots) == 2


def test_review_confirmation_requires_owner_and_csrf(client, settings):
    sign_in(client)
    page = client.get("/week")
    with connect(settings.database_target) as conn:
        review = list_weekly_reviews(conn)[0]
    client.post("/logout", data={"csrf_token": csrf_from(page)})

    unauthenticated = client.post(
        f"/week/reviews/{review.id}/confirm",
        data={"csrf_token": "wrong", "confirmed": "yes"},
        follow_redirects=False,
    )
    assert unauthenticated.status_code == 303
    sign_in(client)
    invalid_csrf = client.post(
        f"/week/reviews/{review.id}/confirm",
        data={"csrf_token": "wrong", "confirmed": "yes"},
    )
    assert invalid_csrf.status_code == 403


def test_current_week_is_private_and_owner_can_change_manual_plan(client, settings):
    assert client.get("/week/current", follow_redirects=False).status_code == 303
    sign_in(client)
    period_start, period_end = current_training_week(date.today())
    page = client.get("/week/current")

    created = client.post(
        "/week/current/sessions",
        data={
            "scheduled_on": period_start.isoformat(),
            "activity_type": "run",
            "title": "Easy run",
            "duration_minutes": "45",
            "distance_km": "7",
            "csrf_token": csrf_from(page),
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with connect(settings.database_target) as conn:
        session = list_planned_sessions(
            conn, start_date=period_start, end_date=period_end
        )[0]

    edit_page = client.get("/week/current")
    updated = client.post(
        f"/week/current/sessions/{session.id}",
        data={
            "scheduled_on": (period_start + timedelta(days=1)).isoformat(),
            "activity_type": "run",
            "title": "Easy run after work",
            "duration_minutes": "40",
            "distance_km": "",
            "change_type": "moved",
            "reason": "schedule",
            "reason_note": "Late meeting",
            "csrf_token": csrf_from(edit_page),
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    skip_page = client.get("/week/current")
    skipped = client.post(
        f"/week/current/sessions/{session.id}/skip",
        data={
            "reason": "recovery",
            "reason_note": "",
            "csrf_token": csrf_from(skip_page),
        },
        follow_redirects=False,
    )
    assert skipped.status_code == 303

    restore_page = client.get("/week/current")
    restored = client.post(
        f"/week/current/sessions/{session.id}/restore",
        data={"csrf_token": csrf_from(restore_page)},
        follow_redirects=False,
    )
    assert restored.status_code == 303
    with connect(settings.database_target) as conn:
        saved = list_planned_sessions(
            conn, start_date=period_start, end_date=period_end
        )[0]
        revisions = list_plan_revisions(conn, session_ids=[session.id])

    assert saved.title == "Easy run after work"
    assert saved.status is PlannedSessionStatus.PLANNED
    assert [revision.change_type for revision in revisions] == [
        PlanChangeType.RESTORED,
        PlanChangeType.SKIPPED,
        PlanChangeType.MOVED,
        PlanChangeType.ADDED,
    ]
    rendered = client.get("/week/current")
    assert "This is a live account of the week, not a daily judgement" in rendered.text
    assert 'data-pending-label="Adding session"' in rendered.text


def test_owner_can_resolve_ambiguous_activity_match(client, settings):
    sign_in(client)
    period_start, period_end = current_training_week(date.today())
    with connect(settings.database_target) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=period_start,
            activity_type=ActivityType.RUN,
            title="Planned run",
        )
        for external_id in ("run-one", "run-two"):
            upsert_activity(
                conn,
                ActivityRecord(
                    source=EvidenceSource.GARMIN,
                    external_id=external_id,
                    recorded_on=period_start,
                    activity_type=ActivityType.RUN,
                    name=external_id,
                ),
            )

    page = client.get("/week/current")
    assert "run-one" in page.text and "run-two" in page.text
    matched = client.post(
        f"/week/current/sessions/{session.id}/match",
        data={
            "activity_identity": "garmin|run-two",
            "csrf_token": csrf_from(page),
        },
        follow_redirects=False,
    )

    assert matched.status_code == 303
    assert "Matched to run-two" in client.get("/week/current").text


def test_owner_can_refresh_current_week_from_garmin(client, monkeypatch):
    sign_in(client)
    page = client.get("/week/current")
    calls = []

    def refresh(_settings, *, user_id, start_date, end_date):
        calls.append((user_id, start_date, end_date))

    monkeypatch.setattr("outset_ready.web.import_hosted_garmin_plan", refresh)
    response = client.post(
        "/week/current/garmin",
        data={"csrf_token": csrf_from(page)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/week/current?notice=garmin-plan-refreshed"
    )
    assert calls[0][0] == "owner"
    assert (calls[0][2] - calls[0][1]).days == 6


def test_invalid_login_is_generic_and_does_not_authenticate(client):
    login_page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": "the-wrong-password",
            "csrf_token": csrf_from(login_page),
        },
    )

    assert response.status_code == 401
    assert "not recognised" in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_rejects_missing_csrf(client):
    response = client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "csrf_token": "wrong",
        },
    )

    assert response.status_code == 403


def test_manual_evidence_is_owner_scoped_and_persisted(client, settings):
    sign_in(client)
    dashboard = client.get("/")
    response = client.post(
        "/evidence",
        data={
            "recorded_on": "2026-09-03",
            "kind": "weight_kg",
            "value": "91.4",
            "note": "",
            "csrf_token": csrf_from(dashboard),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with connect(settings.database_target) as conn:
        evidence = list_recent_evidence(conn, user_id=settings.owner_id)
    assert len(evidence) == 1
    assert evidence[0].value == 91.4
    assert "91.4 kg" in client.get("/").text


def test_manual_evidence_rejects_invalid_csrf(client):
    sign_in(client)
    response = client.post(
        "/evidence",
        data={
            "recorded_on": "2026-09-03",
            "kind": "weight_kg",
            "value": "91.4",
            "note": "",
            "csrf_token": "wrong",
        },
    )

    assert response.status_code == 403


def test_goals_api_returns_401_until_authenticated(client):
    assert client.get("/api/goals").status_code == 401
    sign_in(client)
    response = client.get("/api/goals")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Reach 85 kg"


def test_goal_management_is_private_and_shows_the_active_stack(client):
    assert client.get("/goals", follow_redirects=False).status_code == 303
    sign_in(client)

    response = client.get("/goals")

    assert response.status_code == 200
    assert "What Ready keeps in view" in response.text
    assert "Reach 85 kg" in response.text
    assert "Add target details now" in response.text


def test_owner_can_create_edit_and_archive_a_goal(client, settings):
    sign_in(client)
    page = client.get("/goals")
    created = client.post(
        "/goals",
        data={
            "title": "Walk the South Downs Way",
            "category": "adventure",
            "priority": "future",
            "target_value": "160",
            "target_unit": "km",
            "target_date": "2027-09-01",
            "csrf_token": csrf_from(page),
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert created.headers["location"] == "/goals?notice=created"
    with connect(settings.database_target) as conn:
        goal = next(item for item in list_goals(conn) if item.title.startswith("Walk"))

    edit_page = client.get("/goals")
    updated = client.post(
        f"/goals/{goal.id}",
        data={
            "title": "Complete the South Downs Way",
            "category": "adventure",
            "priority": "current",
            "target_value": "160",
            "target_unit": "km",
            "target_date": "2027-09-01",
            "csrf_token": csrf_from(edit_page),
        },
        follow_redirects=False,
    )

    assert updated.status_code == 303
    with connect(settings.database_target) as conn:
        goals = list_goals(conn)
    assert sum(item.priority is GoalPriority.CURRENT for item in goals) == 1
    assert next(item for item in goals if item.id == goal.id).title.startswith("Complete")

    archive_page = client.get("/goals")
    archived = client.post(
        "/goals/goal-weight-85/archive",
        data={"csrf_token": csrf_from(archive_page)},
        follow_redirects=False,
    )

    assert archived.status_code == 303
    assert archived.headers["location"] == "/goals?notice=archived"
    assert "Archived goals (1)" in client.get(archived.headers["location"]).text


def test_weekly_read_keeps_the_goal_target_that_applied_to_that_week(client, settings):
    sign_in(client)
    period_start, period_end = last_completed_training_week(date.today())
    with connect(settings.database_target) as conn:
        with conn:
            conn.execute(
                "UPDATE goal_revisions SET effective_from = ? WHERE goal_id = ?",
                ((period_start - timedelta(days=1)).isoformat(), "goal-weight-85"),
            )
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=period_end,
                source=EvidenceSource.GARMIN,
                weight_kg=90,
            ),
        )
    page = client.get("/goals")
    response = client.post(
        "/goals/goal-weight-85",
        data={
            "title": "Reach 80 kg",
            "category": "health",
            "priority": "current",
            "target_value": "80",
            "target_unit": "kg",
            "target_date": "",
            "csrf_token": csrf_from(page),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    weekly_page = client.get("/week")
    assert "5.0 kg" in weekly_page.text
    assert "10.0 kg" not in weekly_page.text


def test_current_goal_cannot_be_archived_or_changed_without_csrf(client):
    sign_in(client)
    page = client.get("/goals")

    archive = client.post(
        "/goals/goal-weight-85/archive",
        data={"csrf_token": csrf_from(page)},
    )
    update = client.post(
        "/goals/goal-weight-85",
        data={
            "title": "Reach 84 kg",
            "category": "health",
            "priority": "current",
            "target_value": "84",
            "target_unit": "kg",
            "target_date": "",
            "csrf_token": "wrong",
        },
    )

    assert archive.status_code == 400
    assert "different current goal" in archive.text
    assert update.status_code == 403


def test_slow_forms_disable_repeat_submissions(client):
    sign_in(client)

    connections = client.get("/connections")
    goals = client.get("/goals")
    script = client.get("/static/app.js")

    assert 'data-pending-label="Saving connection"' in connections.text
    assert 'data-pending-label="Adding goal"' in goals.text
    assert script.status_code == 200
    assert 'form.dataset.submitting === "true"' in script.text


def test_connections_page_is_private_and_reports_current_boundary(client):
    assert client.get("/connections", follow_redirects=False).status_code == 303
    sign_in(client)

    response = client.get("/connections")

    assert response.status_code == 200
    assert "Not connected" in response.text
    assert "export-garmin-token" in response.text


def test_owner_can_upload_encrypted_garmin_token(client, settings):
    sign_in(client)
    page = client.get("/connections")

    response = client.post(
        "/connections/garmin/token",
        data={"csrf_token": csrf_from(page)},
        files={
            "token_file": (
                "garmin-token.json",
                VALID_TOKEN.encode("utf-8"),
                "application/json",
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/connections?notice=garmin-token-saved"
    with connect(settings.database_target) as conn:
        connection = fetch_connector_connection(conn, connector="garmin")
        encrypted = load_connector_credentials(conn, connector="garmin")
    assert connection is not None
    assert connection.status is ConnectorConnectionStatus.TOKEN_SAVED
    assert encrypted is not None
    assert "refresh" not in encrypted
    assert "di_refresh_token" in CredentialCipher(ENCRYPTION_KEY).decrypt(encrypted)
    connection_page = client.get(response.headers["location"])
    assert "Garmin token saved" in connection_page.text
    assert "Sync now" in connection_page.text


def test_garmin_token_upload_rejects_invalid_file_and_csrf(client, settings):
    sign_in(client)
    page = client.get("/connections")
    invalid_file = {"token_file": ("token.json", b"{}", "application/json")}

    bad_csrf = client.post(
        "/connections/garmin/token",
        data={"csrf_token": "wrong"},
        files=invalid_file,
    )
    invalid_file = {"token_file": ("token.json", b"{}", "application/json")}
    bad_token = client.post(
        "/connections/garmin/token",
        data={"csrf_token": csrf_from(page)},
        files=invalid_file,
    )

    assert bad_csrf.status_code == 403
    assert bad_token.status_code == 400
    assert "reusable token fields" in bad_token.text
    with connect(settings.database_target) as conn:
        assert fetch_connector_connection(conn, connector="garmin") is None


def test_owner_can_start_hosted_sync(client, monkeypatch):
    sign_in(client)
    page = client.get("/connections")
    calls = []
    monkeypatch.setattr(
        "outset_ready.web.sync_hosted_garmin",
        lambda settings, *, user_id: calls.append((settings, user_id)),
    )

    response = client.post(
        "/connections/garmin/sync",
        data={"csrf_token": csrf_from(page)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/connections?notice=garmin-sync-complete"
    assert calls[0][1] == "owner"


def test_owner_can_backfill_one_resumable_history_batch(client, settings, monkeypatch):
    sign_in(client)
    today = date.today()
    with connect(settings.database_target) as conn:
        save_connector_credentials(
            conn,
            connector="garmin",
            encrypted_credentials="test-ciphertext",
            status=ConnectorConnectionStatus.CONNECTED,
        )
        sync_id = start_connector_sync(
            conn,
            connector="garmin",
            start_date=today - timedelta(days=6),
            end_date=today,
        )
        finish_connector_sync(
            conn,
            sync_id,
            status=ConnectorSyncStatus.COMPLETED,
            daily_records=7,
            activity_records=2,
            warnings=0,
        )
    page = client.get("/connections")
    calls = []

    def sync(_settings, *, user_id, days, end_date):
        calls.append((user_id, days, end_date))

    monkeypatch.setattr("outset_ready.web.sync_hosted_garmin", sync)
    response = client.post(
        "/connections/garmin/backfill",
        data={"csrf_token": csrf_from(page)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/connections?notice=garmin-history-batch-complete"
    )
    assert calls == [("owner", 7, today - timedelta(days=7))]
    assert "7 of 42" in page.text


def test_history_backfill_requires_valid_csrf(client):
    sign_in(client)

    response = client.post(
        "/connections/garmin/backfill",
        data={"csrf_token": "wrong"},
    )

    assert response.status_code == 403


def test_owner_can_remove_garmin_connection(client, settings):
    sign_in(client)
    page = client.get("/connections")
    client.post(
        "/connections/garmin/token",
        data={"csrf_token": csrf_from(page)},
        files={
            "token_file": (
                "garmin-token.json",
                VALID_TOKEN.encode("utf-8"),
                "application/json",
            )
        },
    )
    connected_page = client.get("/connections")

    response = client.post(
        "/connections/garmin/disconnect",
        data={"csrf_token": csrf_from(connected_page)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/connections?notice=garmin-disconnected"
    with connect(settings.database_target) as conn:
        assert fetch_connector_connection(conn, connector="garmin") is None


def test_hosted_sync_reports_reconnect_without_exposing_detail(client, monkeypatch):
    sign_in(client)
    page = client.get("/connections")

    def reject(_settings, *, user_id):
        raise GarminAuthenticationRequiredError("sensitive remote detail")

    monkeypatch.setattr("outset_ready.web.sync_hosted_garmin", reject)
    response = client.post(
        "/connections/garmin/sync",
        data={"csrf_token": csrf_from(page)},
    )

    assert response.status_code == 409
    assert "new token file" in response.text
    assert "sensitive remote detail" not in response.text


def test_logout_clears_owner_session(client):
    sign_in(client)
    dashboard = client.get("/")
    response = client.post(
        "/logout",
        data={"csrf_token": csrf_from(dashboard)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_external_next_url_is_not_followed(client):
    response = sign_in(client, next_path="//attacker.example")

    assert response.headers["location"] == "/"


def test_health_and_database_readiness_are_separate(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "available",
    }


def test_private_responses_are_not_cached(client):
    response = client.get("/login")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_tampered_session_cookie_does_not_authenticate(client):
    sign_in(client)
    cookie = client.cookies.get("outset_ready_session")
    assert cookie
    payload, signature = cookie.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    client.cookies.set(
        "outset_ready_session",
        f"{payload}.{replacement}{signature[1:]}",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303


def test_production_session_cookie_is_secure(settings):
    secure_settings = AppSettings(
        database_target=settings.database_target,
        owner_email=settings.owner_email,
        owner_password_hash=settings.owner_password_hash,
        session_secret=settings.session_secret,
        credential_encryption_key=settings.credential_encryption_key,
        secure_cookies=True,
    )
    with TestClient(
        create_app(settings=secure_settings),
        base_url="https://testserver",
    ) as secure_client:
        response = sign_in(secure_client)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie
