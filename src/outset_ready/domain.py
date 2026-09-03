from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class GoalCategory(StrEnum):
    HEALTH = "health"
    FITNESS = "fitness"
    ADVENTURE = "adventure"


class GoalPriority(StrEnum):
    CURRENT = "current"
    SUPPORTING = "supporting"
    FUTURE = "future"


class EvidenceSource(StrEnum):
    GARMIN = "garmin"
    MANUAL = "manual"


class EvidenceKind(StrEnum):
    WEIGHT_KG = "weight_kg"
    WAIST_CM = "waist_cm"
    ACTIVITY_MINUTES = "activity_minutes"
    SLEEP_HOURS = "sleep_hours"
    ALCOHOL_UNITS = "alcohol_units"
    CALORIES = "calories"
    PROTEIN_G = "protein_g"
    NOTE = "note"


class ActivityType(StrEnum):
    RUN = "run"
    WALK = "walk"
    HIKE = "hike"
    SWIM = "swim"
    STRENGTH = "strength"
    MOBILITY_OR_YOGA = "mobility_or_yoga"
    OTHER = "other"


class ConnectorSyncStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


OPTIONAL_CONTEXT_KINDS = frozenset(
    {
        EvidenceKind.ALCOHOL_UNITS,
        EvidenceKind.CALORIES,
        EvidenceKind.PROTEIN_G,
    }
)


DEFAULT_UNITS = {
    EvidenceKind.WEIGHT_KG: "kg",
    EvidenceKind.WAIST_CM: "cm",
    EvidenceKind.ACTIVITY_MINUTES: "min",
    EvidenceKind.SLEEP_HOURS: "hours",
    EvidenceKind.ALCOHOL_UNITS: "units",
    EvidenceKind.CALORIES: "kcal",
    EvidenceKind.PROTEIN_G: "g",
}


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    category: GoalCategory
    priority: GoalPriority
    sort_order: int
    target_value: float | None = None
    target_unit: str | None = None
    target_date: date | None = None
    supports_goal_id: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    recorded_on: date
    source: EvidenceSource
    kind: EvidenceKind
    value: float | None = None
    unit: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class DailyObservation:
    recorded_on: date
    source: EvidenceSource
    weight_kg: float | None = None
    body_fat_percent: float | None = None
    fat_mass_kg: float | None = None
    lean_mass_kg: float | None = None
    steps: int | None = None
    resting_hr: float | None = None
    sleep_hours: float | None = None
    sleep_score: float | None = None
    stress_score: float | None = None
    hrv_value: float | None = None
    hrv_status: str | None = None
    body_battery_avg: float | None = None
    active_calories: float | None = None
    total_calories: float | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class ActivityRecord:
    source: EvidenceSource
    external_id: str
    recorded_on: date
    activity_type: ActivityType
    name: str | None = None
    duration_seconds: float | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    average_hr: float | None = None
    calories: float | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class ConnectorSync:
    id: str
    connector: str
    status: ConnectorSyncStatus
    started_at: datetime
    finished_at: datetime | None
    start_date: date
    end_date: date
    daily_records: int
    activity_records: int
    warnings: int
    error_message: str | None = None


def validate_evidence(record: EvidenceRecord) -> None:
    if record.kind is EvidenceKind.NOTE:
        if not record.note or not record.note.strip():
            raise ValueError("A note needs some text.")
        return

    if record.value is None:
        raise ValueError("This evidence type needs a value.")
