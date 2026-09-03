import json
from datetime import date
from pathlib import Path

from outset_ready.connectors.garmin.normalise import (
    map_activity_type,
    normalise_activity,
    normalise_daily_observation,
)
from outset_ready.domain import ActivityType, EvidenceSource


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_daily_contract_normalises_known_garmin_shapes():
    observation = normalise_daily_observation(
        date(2026, 9, 3),
        load_fixture("garmin_daily_payloads.json"),
        source_ref="data/raw/garmin/2026-09-03",
    )

    assert observation.source is EvidenceSource.GARMIN
    assert observation.weight_kg == 91.4
    assert observation.body_fat_percent == 29.3
    assert round(observation.fat_mass_kg, 2) == 26.78
    assert observation.steps == 6231
    assert observation.sleep_hours == 7
    assert observation.sleep_score == 82
    assert observation.stress_score == 31
    assert observation.hrv_value == 47
    assert observation.hrv_status == "BALANCED"
    assert observation.body_battery_avg == 50
    assert observation.total_calories == 2716


def test_daily_contract_tolerates_missing_optional_payloads():
    observation = normalise_daily_observation(date(2026, 9, 3), {})
    assert observation.weight_kg is None
    assert observation.sleep_hours is None
    assert observation.hrv_value is None


def test_activity_contract_normalises_known_garmin_shape():
    activity = normalise_activity(load_fixture("garmin_activities.json")[0])

    assert activity is not None
    assert activity.external_id == "12345"
    assert activity.recorded_on == date(2026, 9, 3)
    assert activity.activity_type is ActivityType.RUN
    assert activity.duration_seconds == 3600
    assert activity.distance_meters == 10000
    assert activity.elevation_gain_meters == 123.4
    assert activity.average_hr == 145


def test_activity_contract_skips_records_without_a_date():
    assert normalise_activity({"activityId": 1, "activityName": "No date"}) is None


def test_activity_type_contract_maps_supported_categories():
    assert map_activity_type("trail_running") is ActivityType.RUN
    assert map_activity_type("open_water_swimming") is ActivityType.SWIM
    assert map_activity_type("strength_training") is ActivityType.STRENGTH
    assert map_activity_type("yoga") is ActivityType.MOBILITY_OR_YOGA
    assert map_activity_type("unknown_new_type") is ActivityType.OTHER

