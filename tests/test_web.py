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
    EvidenceSource,
)
from outset_ready.periods import last_completed_training_week
from outset_ready.settings import AppSettings
from outset_ready.storage import (
    connect,
    fetch_connector_connection,
    finish_connector_sync,
    list_recent_evidence,
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
    assert "Your weekly evidence" in response.text
    assert "Progressing" in response.text
    assert "Long easy run" in response.text
    assert "12.0 km" in response.text
    assert "A missing value stays unknown" in response.text


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
