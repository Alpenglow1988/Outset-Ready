from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadinessState(StrEnum):
    PROGRESSING = "Progressing"
    MIXED_SIGNALS = "Mixed signals"
    REVIEW_THE_PLAN = "Review the plan"
    BUILDING_A_PICTURE = "Building a picture"


@dataclass(frozen=True)
class ReadinessSignals:
    evidence_days: int
    goal_trend_supporting: bool | None = None
    planned_sessions: int | None = None
    completed_sessions: int | None = None
    recovery_concern: bool = False


@dataclass(frozen=True)
class ReadinessAssessment:
    state: ReadinessState
    summary: str
    next_step: str


def assess_readiness(signals: ReadinessSignals) -> ReadinessAssessment:
    """Apply transparent V1 rules without asking an AI model to classify raw data."""
    if signals.evidence_days < 3 or signals.goal_trend_supporting is None:
        return ReadinessAssessment(
            ReadinessState.BUILDING_A_PICTURE,
            "There is not enough comparable evidence for a useful read yet.",
            "Keep adding the evidence you already have. Optional context can stay blank.",
        )

    completion_ratio = _completion_ratio(signals)
    if signals.recovery_concern or (
        completion_ratio is not None and completion_ratio < 0.5
    ):
        return ReadinessAssessment(
            ReadinessState.REVIEW_THE_PLAN,
            "Your recent evidence suggests the current plan deserves a calm review.",
            "Check whether the plan still fits the time and recovery you have available.",
        )

    if signals.goal_trend_supporting and (
        completion_ratio is None or completion_ratio >= 0.8
    ):
        return ReadinessAssessment(
            ReadinessState.PROGRESSING,
            "Your recent actions and goal trend are pointing in the same direction.",
            "Keep the current shape of the week and confirm it in the weekly review.",
        )

    return ReadinessAssessment(
        ReadinessState.MIXED_SIGNALS,
        "Some evidence supports the goal and some needs more context.",
        "Use the weekly review to identify the one adjustment that would help most.",
    )


def _completion_ratio(signals: ReadinessSignals) -> float | None:
    if signals.planned_sessions is None or signals.planned_sessions <= 0:
        return None
    completed = max(signals.completed_sessions or 0, 0)
    return completed / signals.planned_sessions

