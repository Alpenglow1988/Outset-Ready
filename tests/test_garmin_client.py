from datetime import date

import pytest

from outset_ready.connectors.garmin.client import (
    GarminAuthenticationRequiredError,
    GarminClient,
    GarminConnectorError,
    MissingGarminCredentialsError,
    OptionalGarminEndpointUnavailable,
)
from outset_ready.connectors.garmin.config import GarminSettings


def settings(tmp_path, *, email="user@example.com", password="secret"):
    return GarminSettings(
        email=email,
        password=password,
        token_dir=tmp_path / "tokens",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "ready.sqlite",
    )


def test_missing_credentials_raise_clear_error(tmp_path):
    client = GarminClient(settings(tmp_path, email=None, password=None))
    with pytest.raises(MissingGarminCredentialsError, match="GARMIN_EMAIL"):
        client.login()


def test_login_reuses_token_store_and_accepts_mfa_callback(monkeypatch, tmp_path):
    calls = {}

    class FakeGarmin:
        def __init__(self, email, password, **kwargs):
            calls.update(email=email, password=password, kwargs=kwargs)

        def login(self, tokenstore=None):
            calls["tokenstore"] = tokenstore

    callback = lambda: "123456"
    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)

    GarminClient(settings(tmp_path)).login(prompt_mfa=callback)

    assert calls["email"] == "user@example.com"
    assert calls["password"] == "secret"
    assert calls["kwargs"]["prompt_mfa"] is callback
    assert calls["tokenstore"] == str(tmp_path / "tokens")


def test_activity_pagination_stops_at_date_and_deduplicates(monkeypatch, tmp_path):
    calls = []

    class FakeGarmin:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, tokenstore=None):
            pass

        def get_activities(self, start=0, limit=2):
            calls.append((start, limit))
            return {
                0: [
                    {"activityId": 1, "startTimeLocal": "2026-09-03T07:00:00"},
                    {"activityId": 2, "startTimeLocal": "2026-09-01T07:00:00"},
                ],
                2: [
                    {"activityId": 2, "startTimeLocal": "2026-09-01T07:00:00"},
                    {"activityId": 3, "startTimeLocal": "2026-08-20T07:00:00"},
                ],
            }.get(start, [])

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path))
    client.login()

    activities = client.fetch_activities_since(date(2026, 8, 21), page_size=2)

    assert calls == [(0, 2), (2, 2)]
    assert [item["activityId"] for item in activities] == [1, 2, 3]


def test_scheduled_workouts_span_months_filter_window_and_deduplicate(
    monkeypatch, tmp_path
):
    calls = []
    duplicate = {
        "id": 2,
        "calendarItemType": "WORKOUT",
        "date": "2026-09-01",
        "workoutId": 102,
    }

    class FakeGarmin:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, tokenstore=None):
            pass

        def get_scheduled_workouts(self, year, month):
            calls.append((year, month))
            return {
                8: {
                    "calendarItems": [
                        {
                            "id": 1,
                            "calendarItemType": "WORKOUT",
                            "date": "2026-08-31",
                            "workoutId": 101,
                        },
                        duplicate,
                        {
                            "id": 9,
                            "calendarItemType": "WORKOUT",
                            "date": "2026-08-20",
                            "workoutId": 109,
                        },
                    ]
                },
                9: {"calendarItems": [duplicate]},
            }[month]

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path))
    client.login()

    workouts = client.fetch_scheduled_workouts(
        date(2026, 8, 30), date(2026, 9, 2)
    )

    assert calls == [(2026, 8), (2026, 9)]
    assert [item["id"] for item in workouts] == [1, 2]


def test_missing_optional_endpoint_has_specific_error(monkeypatch, tmp_path):
    class FakeGarmin:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, tokenstore=None):
            pass

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path))
    client.login()

    with pytest.raises(OptionalGarminEndpointUnavailable, match="HRV"):
        client.fetch_hrv(date(2026, 9, 3))


def test_login_accepts_inline_token_and_exports_rotated_token(monkeypatch, tmp_path):
    calls = {}

    class FakeInternalClient:
        def dumps(self):
            return (
                '{"di_token":"new-access","di_refresh_token":"new-refresh",'
                '"di_client_id":"client"}'
            )

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            calls["init"] = (args, kwargs)
            self.client = FakeInternalClient()

        def login(self, tokenstore=None):
            calls["tokenstore"] = tokenstore

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path, email=None, password=None))
    original = (
        '{"di_token":"access","di_refresh_token":"refresh",'
        '"di_client_id":"client"}'
    )

    client.login(token_bundle=original)

    assert calls["init"] == ((), {})
    assert "refresh" in calls["tokenstore"]
    assert "new-refresh" in client.export_token_bundle()


def test_inline_token_login_failure_requests_reconnection(monkeypatch, tmp_path):
    class GarminConnectAuthenticationError(RuntimeError):
        pass

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, tokenstore=None):
            raise GarminConnectAuthenticationError("API Error 401")

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path, email=None, password=None))
    token = (
        '{"di_token":"access","di_refresh_token":"refresh",'
        '"di_client_id":"client"}'
    )

    with pytest.raises(GarminAuthenticationRequiredError, match="Reconnect Garmin"):
        client.login(token_bundle=token)


def test_inline_token_transient_failure_keeps_connection_reusable(monkeypatch, tmp_path):
    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, tokenstore=None):
            raise RuntimeError("API Error 503")

    monkeypatch.setattr("outset_ready.connectors.garmin.client.Garmin", FakeGarmin)
    client = GarminClient(settings(tmp_path, email=None, password=None))
    token = (
        '{"di_token":"access","di_refresh_token":"refresh",'
        '"di_client_id":"client"}'
    )

    with pytest.raises(GarminConnectorError, match="could not verify") as error:
        client.login(token_bundle=token)
    assert not isinstance(error.value, GarminAuthenticationRequiredError)
