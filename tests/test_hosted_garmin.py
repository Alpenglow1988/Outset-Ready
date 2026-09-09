import json
from datetime import date

import pytest

from outset_ready.auth import hash_password
from outset_ready.connectors.garmin.client import GarminAuthenticationRequiredError
from outset_ready.connectors.garmin.hosted import (
    GarminConnectionUnavailable,
    disconnect_hosted_garmin,
    import_hosted_garmin_plan,
    save_uploaded_token,
    sync_hosted_garmin,
)
from outset_ready.credentials import CredentialCipher, generate_credential_encryption_key
from outset_ready.domain import ConnectorConnectionStatus, ConnectorSyncStatus
from outset_ready.settings import AppSettings
from outset_ready.storage import (
    connect,
    fetch_connector_connection,
    fetch_latest_connector_sync,
    init_db,
    load_connector_credentials,
)
from outset_ready.plans import list_planned_sessions


ORIGINAL_TOKEN = json.dumps(
    {
        "di_token": "original-access",
        "di_refresh_token": "original-refresh",
        "di_client_id": "client",
    }
)
UPDATED_TOKEN = json.dumps(
    {
        "di_token": "updated-access",
        "di_refresh_token": "updated-refresh",
        "di_client_id": "client",
    }
)


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(
        database_target=tmp_path / "ready.sqlite",
        owner_email="ian@example.com",
        owner_password_hash=hash_password("a-long-test-password"),
        session_secret="test-session-secret-that-is-long-enough",
        credential_encryption_key=generate_credential_encryption_key(),
        secure_cookies=False,
    )
    init_db(value.database_target, owner_email=value.owner_email)
    return value


class HostedFixtureClient:
    instances = []

    def __init__(self, _settings):
        self.__class__.instances.append(self)
        self.received_token = None

    def login(self, prompt_mfa=None, *, token_bundle=None):
        self.received_token = token_bundle

    def export_token_bundle(self):
        return UPDATED_TOKEN

    def fetch_user_summary(self, payload_date):
        return {"calendarDate": payload_date.isoformat(), "totalSteps": 1000}

    def fetch_body_composition(self, _payload_date):
        return {}

    def fetch_sleep(self, _payload_date):
        return {}

    def fetch_stress(self, _payload_date):
        return {}

    def fetch_hrv(self, _payload_date):
        return {}

    def fetch_activities_since(self, _start_date, *, page_size):
        return []

    def fetch_scheduled_workouts(self, start_date, _end_date):
        return [
            {
                "id": 72001,
                "calendarItemType": "WORKOUT",
                "date": start_date.isoformat(),
                "workoutId": 81001,
                "workoutName": "Easy run",
                "sportType": {"sportTypeKey": "running"},
            }
        ]


def test_uploaded_token_is_encrypted_and_hosted_sync_rotates_it(settings):
    HostedFixtureClient.instances.clear()
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)

    with connect(settings.database_target) as conn:
        stored = load_connector_credentials(conn, connector="garmin")
        before = fetch_connector_connection(conn, connector="garmin")
    assert stored is not None
    assert "original-refresh" not in stored
    assert before is not None
    assert before.status is ConnectorConnectionStatus.TOKEN_SAVED

    stats = sync_hosted_garmin(
        settings,
        user_id="owner",
        days=1,
        client_factory=HostedFixtureClient,
    )

    assert stats.start_date == stats.end_date == date.today()
    assert HostedFixtureClient.instances[0].received_token is not None
    assert "original-refresh" in HostedFixtureClient.instances[0].received_token
    with connect(settings.database_target) as conn:
        rotated = load_connector_credentials(conn, connector="garmin")
        after = fetch_connector_connection(conn, connector="garmin")
        latest_sync = fetch_latest_connector_sync(conn, "garmin")
    assert rotated is not None
    assert "updated-refresh" in CredentialCipher(
        settings.credential_encryption_key
    ).decrypt(rotated)
    assert after is not None
    assert after.status is ConnectorConnectionStatus.CONNECTED
    assert after.connected_at is not None
    assert latest_sync is not None
    assert latest_sync.status is ConnectorSyncStatus.COMPLETED


def test_rejected_token_marks_connection_for_reconnect(settings):
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)

    class RejectingClient:
        def __init__(self, _settings):
            pass

        def login(self, prompt_mfa=None, *, token_bundle=None):
            raise GarminAuthenticationRequiredError("rejected")

    with pytest.raises(GarminAuthenticationRequiredError):
        sync_hosted_garmin(
            settings,
            user_id="owner",
            days=1,
            client_factory=RejectingClient,
        )

    with connect(settings.database_target) as conn:
        connection = fetch_connector_connection(conn, connector="garmin")
        latest_sync = fetch_latest_connector_sync(conn, "garmin")
    assert connection is not None
    assert connection.status is ConnectorConnectionStatus.RECONNECT_REQUIRED
    assert latest_sync is not None
    assert latest_sync.error_message == "Garmin requires reconnection."
    assert "rejected" not in latest_sync.error_message


def test_wrong_encryption_key_requires_reconnect(settings):
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)
    changed_settings = AppSettings(
        database_target=settings.database_target,
        owner_email=settings.owner_email,
        owner_password_hash=settings.owner_password_hash,
        session_secret=settings.session_secret,
        credential_encryption_key=generate_credential_encryption_key(),
        secure_cookies=False,
    )

    with pytest.raises(GarminConnectionUnavailable):
        sync_hosted_garmin(changed_settings, user_id="owner")

    with connect(settings.database_target) as conn:
        connection = fetch_connector_connection(conn, connector="garmin")
    assert connection is not None
    assert connection.status is ConnectorConnectionStatus.RECONNECT_REQUIRED

    with pytest.raises(GarminConnectionUnavailable, match="replacement token"):
        sync_hosted_garmin(changed_settings, user_id="owner")


def test_disconnect_removes_token_but_keeps_sync_history(settings):
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)
    sync_hosted_garmin(
        settings,
        user_id="owner",
        days=1,
        client_factory=HostedFixtureClient,
    )

    disconnect_hosted_garmin(settings, user_id="owner")

    with connect(settings.database_target) as conn:
        assert fetch_connector_connection(conn, connector="garmin") is None
        assert load_connector_credentials(conn, connector="garmin") is None
        assert fetch_latest_connector_sync(conn, "garmin") is not None


def test_hosted_sync_accepts_a_bounded_history_window(settings):
    HostedFixtureClient.instances.clear()
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)

    stats = sync_hosted_garmin(
        settings,
        user_id="owner",
        days=7,
        end_date=date(2026, 8, 30),
        client_factory=HostedFixtureClient,
    )

    assert stats.start_date == date(2026, 8, 24)
    assert stats.end_date == date(2026, 8, 30)


def test_hosted_plan_import_persists_snapshot_and_rotates_token(settings):
    HostedFixtureClient.instances.clear()
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)

    stats = import_hosted_garmin_plan(
        settings,
        user_id="owner",
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 13),
        client_factory=HostedFixtureClient,
    )

    assert stats.scheduled_records == 1
    assert stats.automatic_matches == 0
    assert HostedFixtureClient.instances[0].received_token is not None
    with connect(settings.database_target) as conn:
        sessions = list_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        )
        rotated = load_connector_credentials(conn, connector="garmin")
    assert [session.title for session in sessions] == ["Easy run"]
    assert rotated is not None
    assert "updated-refresh" in CredentialCipher(
        settings.credential_encryption_key
    ).decrypt(rotated)


def test_hosted_plan_auth_failure_marks_connection_for_reconnect(settings):
    save_uploaded_token(settings, user_id="owner", token_payload=ORIGINAL_TOKEN)

    class RejectingPlanClient:
        def __init__(self, _settings):
            pass

        def login(self, prompt_mfa=None, *, token_bundle=None):
            raise GarminAuthenticationRequiredError("rejected")

    with pytest.raises(GarminAuthenticationRequiredError):
        import_hosted_garmin_plan(
            settings,
            user_id="owner",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            client_factory=RejectingPlanClient,
        )

    with connect(settings.database_target) as conn:
        connection = fetch_connector_connection(conn, connector="garmin")
    assert connection is not None
    assert connection.status is ConnectorConnectionStatus.RECONNECT_REQUIRED
