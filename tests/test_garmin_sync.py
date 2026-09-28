import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from outset_ready.connectors.garmin.client import (
    GarminAuthenticationRequiredError,
    MissingGarminCredentialsError,
)
from outset_ready.connectors.garmin.config import GarminSettings
from outset_ready.connectors.garmin.sync import sync_garmin
from outset_ready.domain import ConnectorSyncStatus, DailyObservation, EvidenceSource
from outset_ready.storage import (
    connect, fetch_latest_connector_sync, init_db,
    list_daily_observations_between, upsert_daily_observation,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def settings(tmp_path):
    return GarminSettings(
        email="user@example.com",
        password="secret",
        token_dir=tmp_path / "tokens",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "outset_ready.sqlite",
    )


class FixtureClient:
    def __init__(self, _settings):
        self.daily = load_fixture("garmin_daily_payloads.json")
        self.activities = load_fixture("garmin_activities.json")
        self.activity_start_date = None
        self.page_size = None

    def login(self, prompt_mfa=None, *, token_bundle=None):
        self.prompt_mfa = prompt_mfa
        self.token_bundle = token_bundle

    def fetch_user_summary(self, payload_date):
        return {**self.daily["user_summary"], "calendarDate": payload_date.isoformat()}

    def fetch_body_composition(self, _payload_date):
        return self.daily["body_composition"]

    def fetch_sleep(self, _payload_date):
        return self.daily["sleep"]

    def fetch_stress(self, payload_date):
        if payload_date == date(2026, 9, 2):
            from outset_ready.connectors.garmin.client import GarminConnectorError

            raise GarminConnectorError("synthetic endpoint failure")
        return self.daily["stress"]

    def fetch_hrv(self, _payload_date):
        return self.daily["hrv"]

    def fetch_activities_since(self, start_date, *, page_size):
        self.activity_start_date = start_date
        self.page_size = page_size
        return self.activities

    def fetch_activities_between(self, start_date, end_date):
        self.activity_start_date = start_date
        self.activity_end_date = end_date
        return self.activities


def test_sync_is_idempotent_and_records_partial_endpoint_failure(tmp_path):
    garmin_settings = settings(tmp_path)
    clients = []

    def client_factory(value):
        client = FixtureClient(value)
        clients.append(client)
        return client

    first = sync_garmin(
        garmin_settings,
        days=2,
        end_date=date(2026, 9, 3),
        activity_page_size=25,
        client_factory=client_factory,
    )
    second = sync_garmin(
        garmin_settings,
        days=2,
        end_date=date(2026, 9, 3),
        activity_page_size=25,
        client_factory=client_factory,
    )

    assert first.daily_records == second.daily_records == 2
    assert first.activity_records == second.activity_records == 2
    assert len(first.warnings) == 1
    assert clients[0].activity_start_date == date(2026, 8, 21)
    assert clients[0].page_size == 25

    with sqlite3.connect(garmin_settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_observations").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM connector_syncs").fetchone()[0] == 2

    with connect(garmin_settings.db_path) as conn:
        latest = fetch_latest_connector_sync(conn, "garmin")
    assert latest is not None
    assert latest.status is ConnectorSyncStatus.COMPLETED_WITH_WARNINGS
    assert latest.warnings == 1

    raw_summary = (
        garmin_settings.raw_dir / "2026-09-03" / "user_summary.json"
    )
    assert raw_summary.exists()
    assert json.loads(raw_summary.read_text(encoding="utf-8"))["calendarDate"] == "2026-09-03"


def test_calendar_sync_uses_exact_week_range_for_older_activities(tmp_path):
    clients = []

    def client_factory(value):
        client = FixtureClient(value)
        clients.append(client)
        return client

    stats = sync_garmin(
        settings(tmp_path), days=7, end_date=date(2025, 1, 12),
        bounded_activity_range=True, client_factory=client_factory,
        save_raw_payloads=False,
    )

    assert stats.start_date == date(2025, 1, 6)
    assert clients[0].activity_start_date == date(2025, 1, 6)
    assert clients[0].activity_end_date == date(2025, 1, 12)
    assert stats.activity_records == 0  # fixture activities belong to another week


def test_calendar_retry_keeps_previous_day_when_optional_endpoint_fails(tmp_path):
    garmin_settings = settings(tmp_path)
    init_db(garmin_settings.db_path)
    with connect(garmin_settings.db_path) as conn:
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=date(2026, 9, 2), source=EvidenceSource.GARMIN,
                weight_kg=92.4, stress_score=34,
            ),
        )

    result = sync_garmin(
        garmin_settings, days=2, end_date=date(2026, 9, 3),
        bounded_activity_range=True, client_factory=FixtureClient,
        save_raw_payloads=False,
    )
    with connect(garmin_settings.db_path) as conn:
        old_day = list_daily_observations_between(
            conn, start_date=date(2026, 9, 2), end_date=date(2026, 9, 2),
        )[0]

    assert result.warnings
    assert old_day.weight_kg == 92.4
    assert old_day.stress_score == 34


def test_login_failure_is_recorded_without_exposing_credentials(tmp_path):
    garmin_settings = settings(tmp_path)

    class FailingClient:
        def __init__(self, _settings):
            pass

        def login(self, prompt_mfa=None, *, token_bundle=None):
            raise MissingGarminCredentialsError("credentials missing")

    with pytest.raises(MissingGarminCredentialsError):
        sync_garmin(
            garmin_settings,
            end_date=date(2026, 9, 3),
            client_factory=FailingClient,
        )

    with connect(garmin_settings.db_path) as conn:
        latest = fetch_latest_connector_sync(conn, "garmin")
    assert latest is not None
    assert latest.status is ConnectorSyncStatus.FAILED
    assert latest.error_message == "Garmin sync could not finish."
    assert "secret" not in latest.error_message


def test_authentication_failure_during_endpoint_stops_sync(tmp_path):
    garmin_settings = settings(tmp_path)

    class ExpiredClient(FixtureClient):
        def fetch_user_summary(self, payload_date):
            raise GarminAuthenticationRequiredError("expired")

    with pytest.raises(GarminAuthenticationRequiredError):
        sync_garmin(
            garmin_settings,
            days=1,
            end_date=date(2026, 9, 3),
            client_factory=ExpiredClient,
        )

    with connect(garmin_settings.db_path) as conn:
        latest = fetch_latest_connector_sync(conn, "garmin")
    assert latest is not None
    assert latest.status is ConnectorSyncStatus.FAILED
    assert latest.error_message == "Garmin requires reconnection."
