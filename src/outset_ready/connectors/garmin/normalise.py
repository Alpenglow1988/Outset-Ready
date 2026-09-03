from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    DailyObservation,
    EvidenceSource,
)


def normalise_daily_observation(
    payload_date: date,
    payloads: dict[str, Any],
    *,
    source_ref: str | None = None,
) -> DailyObservation:
    user_summary = _mapping(payloads.get("user_summary"))
    body_composition = _mapping(payloads.get("body_composition"))
    sleep = _mapping(payloads.get("sleep"))
    stress = _mapping(payloads.get("stress"))
    hrv = _mapping(payloads.get("hrv"))

    weight_kg, body_fat_percent = _extract_body_composition(body_composition)
    fat_mass_kg, lean_mass_kg = calculate_body_composition(
        weight_kg,
        body_fat_percent,
    )

    daily_sleep = _first_dict(sleep, ("dailySleepDTO",))
    sleep_duration, duration_is_millis = _extract_duration(
        daily_sleep,
        (
            "sleepTimeSeconds",
            "sleepDurationSeconds",
            "awakeSleepSeconds",
            "sleepTimeMillis",
            "sleepDurationMillis",
        ),
    )
    hrv_summary = _first_dict(hrv, ("hrvSummary",))

    return DailyObservation(
        recorded_on=payload_date,
        source=EvidenceSource.GARMIN,
        weight_kg=weight_kg,
        body_fat_percent=body_fat_percent,
        fat_mass_kg=fat_mass_kg,
        lean_mass_kg=lean_mass_kg,
        steps=_as_int(_first(user_summary, ("totalSteps", "steps", "dailySteps"))),
        resting_hr=_as_float(
            _first(
                user_summary or daily_sleep,
                ("restingHeartRate", "lastSevenDaysAvgRestingHeartRate"),
            )
        ),
        sleep_hours=_duration_to_hours(sleep_duration, duration_is_millis),
        sleep_score=_as_float(_sleep_score(daily_sleep)),
        stress_score=_as_float(
            _first(stress, ("avgStressLevel", "averageStressLevel"))
        ),
        hrv_value=_as_float(_first(hrv_summary, ("lastNightAvg", "weeklyAvg"))),
        hrv_status=_as_text(_first(hrv_summary, ("status",))),
        body_battery_avg=_body_battery_average(user_summary, stress),
        active_calories=_as_float(
            _first(
                user_summary,
                (
                    "activeKilocalories",
                    "wellnessActiveKilocalories",
                    "burnedKilocalories",
                ),
            )
        ),
        total_calories=_as_float(
            _first(
                user_summary,
                ("totalKilocalories", "wellnessKilocalories", "burnedKilocalories"),
            )
        ),
        source_ref=source_ref,
    )


def calculate_body_composition(
    weight_kg: float | None,
    body_fat_percent: float | None,
) -> tuple[float | None, float | None]:
    if weight_kg is None or body_fat_percent is None:
        return None, None
    fat_mass_kg = weight_kg * body_fat_percent / 100
    return fat_mass_kg, weight_kg - fat_mass_kg


def map_activity_type(raw_type: Any) -> ActivityType:
    value = _activity_type_value(raw_type)
    if value is None:
        return ActivityType.OTHER

    normalised = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if normalised in {
        "running",
        "run",
        "trail_running",
        "trailrun",
        "treadmill_running",
        "indoor_running",
    } or "running" in normalised or normalised.endswith("_run"):
        return ActivityType.RUN
    if normalised in {
        "walking",
        "walk",
        "casual_walking",
        "treadmill_walking",
    } or "walk" in normalised:
        return ActivityType.WALK
    if normalised in {"hiking", "hike", "trail_hiking"} or "hike" in normalised:
        return ActivityType.HIKE
    if normalised in {
        "swimming",
        "swim",
        "lap_swimming",
        "open_water_swimming",
        "pool_swimming",
    } or "swim" in normalised:
        return ActivityType.SWIM
    if normalised in {
        "strength_training",
        "cardio_strength",
        "strength",
        "weight_training",
        "resistance_training",
        "bodybuilding",
    } or ("strength" in normalised and "running" not in normalised):
        return ActivityType.STRENGTH
    if normalised in {
        "yoga",
        "pilates",
        "breathwork",
        "mobility",
        "mobility_or_yoga",
        "stretching",
    } or any(
        token in normalised for token in ("yoga", "pilates", "breath", "mobility")
    ):
        return ActivityType.MOBILITY_OR_YOGA
    return ActivityType.OTHER


def extract_activity_date(activity: dict[str, Any]) -> str | None:
    for key in (
        "startTimeLocal",
        "startTimeGMT",
        "beginTimestamp",
        "activityDate",
        "calendarDate",
        "startDate",
    ):
        parsed = _parse_datetime(activity.get(key))
        if parsed is not None:
            return parsed.date().isoformat()
    return None


def normalise_activity(
    activity: dict[str, Any],
    *,
    source_ref: str | None = None,
) -> ActivityRecord | None:
    activity_date = extract_activity_date(activity)
    if activity_date is None:
        return None

    external_id = _first(activity, ("activityId", "activity_id", "id"))
    if external_id is None:
        external_id = _fallback_activity_id(activity, activity_date)

    return ActivityRecord(
        source=EvidenceSource.GARMIN,
        external_id=str(external_id),
        recorded_on=date.fromisoformat(activity_date),
        activity_type=map_activity_type(
            _first(activity, ("activityType", "activity_type", "type"))
        ),
        name=_as_text(_first(activity, ("activityName", "name", "title"))),
        duration_seconds=_numeric_with_units(
            activity,
            (
                "durationSeconds",
                "durationInSeconds",
                "duration",
                "movingDuration",
                "movingDurationSeconds",
                "elapsedDuration",
                "elapsedDurationSeconds",
                "durationMillis",
                "durationInMilliseconds",
                "movingDurationInMilliseconds",
                "elapsedDurationInMilliseconds",
            ),
            millis=True,
        ),
        distance_meters=_distance_meters(activity),
        elevation_gain_meters=_numeric(
            activity,
            (
                "elevationGain",
                "elevation_gain",
                "elevationGainMeters",
                "ascent",
                "totalElevationGain",
            ),
        ),
        average_hr=_numeric(
            activity,
            ("averageHR", "avgHr", "averageHeartRate", "meanHR", "averageHeartrate"),
        ),
        calories=_numeric(
            activity,
            ("calories", "kilocalories", "activeKilocalories", "calorieValue"),
        ),
        source_ref=source_ref,
    )


def _extract_body_composition(
    body_composition: dict[str, Any],
) -> tuple[float | None, float | None]:
    candidates: list[dict[str, Any]] = []
    total_average = _mapping(body_composition.get("totalAverage"))
    if total_average:
        candidates.append(total_average)
    date_weights = body_composition.get("dateWeightList")
    if isinstance(date_weights, list):
        candidates.extend(item for item in reversed(date_weights) if isinstance(item, dict))

    for candidate in candidates:
        source_key, weight_value = _first_key_value(
            candidate,
            ("weight", "weightKg", "bodyWeight"),
        )
        weight_kg = _as_float(weight_value)
        if source_key == "weight" and weight_kg is not None and weight_kg > 1000:
            weight_kg /= 1000
        body_fat = _as_float(
            _first(candidate, ("bodyFat", "bodyFatPercent", "percentFat"))
        )
        if weight_kg is not None or body_fat is not None:
            return weight_kg, body_fat
    return None, None


def _body_battery_average(
    user_summary: dict[str, Any],
    stress: dict[str, Any],
) -> float | None:
    values = stress.get("bodyBatteryValuesArray")
    if isinstance(values, list):
        samples = [
            sample
            for item in values
            if isinstance(item, list) and len(item) >= 3
            if (sample := _as_float(item[2])) is not None
        ]
        if samples:
            return mean(samples)
    return _as_float(
        _first(
            user_summary,
            ("bodyBatteryMostRecentValue", "bodyBatteryAtWakeTime"),
        )
    )


def _sleep_score(daily_sleep: dict[str, Any]) -> Any:
    scores = _mapping(daily_sleep.get("sleepScores"))
    overall = _mapping(scores.get("overall"))
    return _first(overall, ("value", "score"))


def _extract_duration(
    mapping: dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[float | None, bool]:
    key, value = _first_key_value(mapping, keys)
    return _as_float(value), bool(key and "millis" in key.lower())


def _duration_to_hours(value: float | None, is_millis: bool) -> float | None:
    if value is None:
        return None
    return value / (3_600_000 if is_millis else 3_600)


def _activity_type_value(raw_type: Any) -> Any:
    if isinstance(raw_type, dict):
        return _first(
            raw_type,
            ("typeKey", "typeName", "activityType", "activityTypeName", "name", "type", "key"),
        )
    return raw_type


def _numeric(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    _, value = _first_key_value(mapping, keys)
    return _as_float(value)


def _numeric_with_units(
    mapping: dict[str, Any],
    keys: tuple[str, ...],
    *,
    millis: bool,
) -> float | None:
    key, value = _first_key_value(mapping, keys)
    number = _as_float(value)
    if number is None:
        return None
    if millis and key and "millis" in key.lower():
        return number / 1000
    return number


def _distance_meters(activity: dict[str, Any]) -> float | None:
    key, value = _first_key_value(
        activity,
        (
            "distanceMeters",
            "distanceInMeters",
            "totalDistanceMeters",
            "distance",
            "distanceKm",
            "distanceInKilometers",
            "distanceMiles",
            "distanceInMiles",
        ),
    )
    distance = _as_float(value)
    if distance is None:
        return None
    if key in {"distanceKm", "distanceInKilometers"}:
        return distance * 1000
    if key in {"distanceMiles", "distanceInMiles"}:
        return distance * 1609.344
    return distance


def _fallback_activity_id(activity: dict[str, Any], activity_date: str) -> str:
    name = _first(activity, ("activityName", "name", "title")) or ""
    duration = _numeric_with_units(
        activity,
        ("durationSeconds", "durationInSeconds", "duration", "durationInMilliseconds"),
        millis=True,
    )
    distance = _distance_meters(activity)
    payload = f"{activity_date}|{name}|{duration}|{distance}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1_000_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_datetime(int(text))
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if len(text) == 10:
            try:
                return datetime.combine(
                    date.fromisoformat(text),
                    datetime.min.time(),
                    tzinfo=UTC,
                )
            except ValueError:
                return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_dict(mapping: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return _mapping(_first(mapping, keys))


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _first_key_value(
    mapping: dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[str | None, Any]:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return key, mapping[key]
    return None, None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return int(number) if number is not None else None


def _as_text(value: Any) -> str | None:
    return str(value) if value is not None else None

