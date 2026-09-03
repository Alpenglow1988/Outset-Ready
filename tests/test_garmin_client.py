from datetime import date

import pytest

from outset_ready.connectors.garmin.client import (
    GarminClient,
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

