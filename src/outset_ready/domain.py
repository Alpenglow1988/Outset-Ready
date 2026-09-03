from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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


def validate_evidence(record: EvidenceRecord) -> None:
    if record.kind is EvidenceKind.NOTE:
        if not record.note or not record.note.strip():
            raise ValueError("A note needs some text.")
        return

    if record.value is None:
        raise ValueError("This evidence type needs a value.")

