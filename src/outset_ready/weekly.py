from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    DailyObservation,
    EvidenceKind,
    EvidenceRecord,
    EvidenceSource,
)
from outset_ready.readiness import ReadinessAssessment, ReadinessState
from outset_ready.plans import PlanWeek, build_plan_week
from outset_ready.storage import (
    DEFAULT_OWNER_ID,
    list_activities_between,
    list_daily_observations_between,
    list_evidence_between,
    list_latest_evidence_of_kind,
)


WEEK_DAYS = 7
MONTH_DAYS = 30
HISTORY_QUERY_DAYS = 35
RUN_LONG_DURATION_SECONDS = 75 * 60
RUN_LONG_DISTANCE_METERS = 10_000
HIKE_LONG_DURATION_SECONDS = 90 * 60
LOW_SLEEP_HOURS = 6.5
RESTING_HR_DELTA_THRESHOLD = 5.0
FAST_LOSS_THRESHOLD_KG_PER_WEEK = -0.9
EXPECTED_LOSS_MIN_KG_PER_WEEK = -0.75
EXPECTED_LOSS_MAX_KG_PER_WEEK = -0.25
POSITIVE_HRV_STATUSES = frozenset(
    {"BALANCED", "GOOD", "NORMAL", "OPTIMAL", "STABLE", "MAINTAINING"}
)
NEGATIVE_HRV_HINTS = frozenset(
    {"LOW", "LOWER", "LOWER_THAN_USUAL", "UNBALANCED", "POOR", "BELOW"}
)


@dataclass(frozen=True)
class WeightMetrics:
    latest_weight_kg: float | None
    latest_weight_date: date | None
    seven_day_average_weight_kg: float | None
    previous_seven_day_average_weight_kg: float | None
    weekly_weight_change_kg: float | None
    thirty_day_weight_change_kg: float | None
    kg_remaining_to_target_weight: float | None
    current_sample_days: int
    previous_sample_days: int


@dataclass(frozen=True)
class BodyCompositionMetrics:
    latest_body_fat_percent: float | None
    latest_fat_mass_kg: float | None
    latest_lean_mass_kg: float | None
    latest_date: date | None


@dataclass(frozen=True)
class WaistMetrics:
    latest_waist_cm: float | None
    latest_date: date | None
    previous_waist_cm: float | None
    previous_date: date | None
    change_cm: float | None


@dataclass(frozen=True)
class TrainingMetrics:
    activity_count: int
    runs_completed: int
    strength_sessions_completed: int
    swims_completed: int
    walks_hikes_completed: int
    long_run_or_hike_done: bool
    total_running_distance_meters: float | None
    total_running_duration_seconds: float | None
    total_activity_time_seconds: float | None
    longest_activity_duration_seconds: float | None
    longest_run_hike_duration_seconds: float | None
    manual_activity_minutes: float | None


@dataclass(frozen=True)
class RecoveryMetrics:
    average_sleep_hours: float | None
    latest_resting_hr: float | None
    resting_hr_baseline_30d: float | None
    resting_hr_vs_baseline_30d: float | None
    latest_hrv_value: float | None
    latest_hrv_status: str | None
    average_stress_score: float | None
    average_body_battery: float | None


@dataclass(frozen=True)
class OptionalContextMetrics:
    alcohol_units: float | None
    average_calories: float | None
    average_protein_g: float | None
    notes: tuple[EvidenceRecord, ...]


@dataclass(frozen=True)
class MetricCoverage:
    key: str
    label: str
    observed_days: int
    expected_days: int = WEEK_DAYS


@dataclass(frozen=True)
class WeeklyRead:
    period_start: date
    period_end: date
    assessment: ReadinessAssessment
    weight: WeightMetrics
    body_composition: BodyCompositionMetrics
    waist: WaistMetrics
    training: TrainingMetrics
    recovery: RecoveryMetrics
    optional_context: OptionalContextMetrics
    plan: PlanWeek
    coverage: tuple[MetricCoverage, ...]
    activities: tuple[ActivityRecord, ...]


def build_weekly_read(
    conn,
    *,
    period_start: date,
    period_end: date,
    target_weight_kg: float | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> WeeklyRead:
    if period_end < period_start:
        raise ValueError("period_end cannot be before period_start")
    if (period_end - period_start).days != WEEK_DAYS - 1:
        raise ValueError("A weekly read requires seven consecutive days")

    history_start = period_end - timedelta(days=HISTORY_QUERY_DAYS)
    observations = list_daily_observations_between(
        conn,
        start_date=history_start,
        end_date=period_end,
        user_id=user_id,
    )
    evidence = list_evidence_between(
        conn,
        start_date=history_start,
        end_date=period_end,
        user_id=user_id,
    )
    waist_evidence = list_latest_evidence_of_kind(
        conn,
        kind=EvidenceKind.WAIST_CM,
        through_date=period_end,
        limit=2,
        user_id=user_id,
    )
    activities = list_activities_between(
        conn,
        start_date=period_start,
        end_date=period_end,
        user_id=user_id,
    )

    weight = calculate_weight_metrics(
        observations,
        evidence,
        period_start=period_start,
        period_end=period_end,
        target_weight_kg=target_weight_kg,
    )
    body_composition = calculate_body_composition_metrics(
        observations,
        through_date=period_end,
    )
    waist = calculate_waist_metrics(waist_evidence)
    training = calculate_training_metrics(
        activities,
        evidence,
        period_start=period_start,
        period_end=period_end,
    )
    recovery = calculate_recovery_metrics(
        observations,
        evidence,
        period_start=period_start,
        period_end=period_end,
    )
    optional_context = calculate_optional_context(
        evidence,
        period_start=period_start,
        period_end=period_end,
    )
    plan = build_plan_week(
        conn,
        period_start=period_start,
        period_end=period_end,
        user_id=user_id,
    )
    assessment = assess_weekly_read(weight, recovery, plan)
    coverage = calculate_metric_coverage(
        observations,
        evidence,
        period_start=period_start,
        period_end=period_end,
    )
    return WeeklyRead(
        period_start=period_start,
        period_end=period_end,
        assessment=assessment,
        weight=weight,
        body_composition=body_composition,
        waist=waist,
        training=training,
        recovery=recovery,
        optional_context=optional_context,
        plan=plan,
        coverage=coverage,
        activities=tuple(activities),
    )


def calculate_weight_metrics(
    observations: list[DailyObservation],
    evidence: list[EvidenceRecord],
    *,
    period_start: date,
    period_end: date,
    target_weight_kg: float | None = None,
) -> WeightMetrics:
    values = _merged_daily_values(
        observations,
        evidence,
        observation_attribute="weight_kg",
        manual_kind=EvidenceKind.WEIGHT_KG,
    )
    current_values = _values_between(values, period_start, period_end)
    previous_start = period_start - timedelta(days=WEEK_DAYS)
    previous_end = period_start - timedelta(days=1)
    previous_values = _values_between(values, previous_start, previous_end)
    latest_date = max((item for item in values if item <= period_end), default=None)
    latest_weight = values.get(latest_date) if latest_date else None
    current_average = _average(current_values)
    previous_average = _average(previous_values)
    weekly_change = (
        current_average - previous_average
        if current_average is not None and previous_average is not None
        else None
    )
    baseline_cutoff = period_end - timedelta(days=MONTH_DAYS)
    baseline_date = max(
        (item for item in values if item <= baseline_cutoff),
        default=None,
    )
    thirty_day_change = (
        latest_weight - values[baseline_date]
        if latest_weight is not None and baseline_date is not None
        else None
    )
    remaining = (
        max(latest_weight - target_weight_kg, 0.0)
        if latest_weight is not None and target_weight_kg is not None
        else None
    )
    return WeightMetrics(
        latest_weight_kg=latest_weight,
        latest_weight_date=latest_date,
        seven_day_average_weight_kg=current_average,
        previous_seven_day_average_weight_kg=previous_average,
        weekly_weight_change_kg=weekly_change,
        thirty_day_weight_change_kg=thirty_day_change,
        kg_remaining_to_target_weight=remaining,
        current_sample_days=len(current_values),
        previous_sample_days=len(previous_values),
    )


def calculate_body_composition_metrics(
    observations: list[DailyObservation],
    *,
    through_date: date,
) -> BodyCompositionMetrics:
    matching = [
        item
        for item in observations
        if item.recorded_on <= through_date and item.body_fat_percent is not None
    ]
    if not matching:
        return BodyCompositionMetrics(None, None, None, None)
    latest = max(matching, key=lambda item: item.recorded_on)
    return BodyCompositionMetrics(
        latest_body_fat_percent=_float_or_none(latest.body_fat_percent),
        latest_fat_mass_kg=_float_or_none(latest.fat_mass_kg),
        latest_lean_mass_kg=_float_or_none(latest.lean_mass_kg),
        latest_date=latest.recorded_on,
    )


def calculate_waist_metrics(evidence: list[EvidenceRecord]) -> WaistMetrics:
    measured = [item for item in evidence if item.value is not None]
    latest = measured[0] if measured else None
    previous = measured[1] if len(measured) > 1 else None
    latest_value = _float_or_none(latest.value) if latest else None
    previous_value = _float_or_none(previous.value) if previous else None
    change = (
        latest_value - previous_value
        if latest_value is not None and previous_value is not None
        else None
    )
    return WaistMetrics(
        latest_waist_cm=latest_value,
        latest_date=latest.recorded_on if latest else None,
        previous_waist_cm=previous_value,
        previous_date=previous.recorded_on if previous else None,
        change_cm=change,
    )


def calculate_training_metrics(
    activities: list[ActivityRecord],
    evidence: list[EvidenceRecord],
    *,
    period_start: date,
    period_end: date,
) -> TrainingMetrics:
    period_activities = [
        item for item in activities if period_start <= item.recorded_on <= period_end
    ]
    runs = [item for item in period_activities if item.activity_type is ActivityType.RUN]
    strength = [
        item for item in period_activities if item.activity_type is ActivityType.STRENGTH
    ]
    swims = [item for item in period_activities if item.activity_type is ActivityType.SWIM]
    walks_hikes = [
        item
        for item in period_activities
        if item.activity_type in {ActivityType.WALK, ActivityType.HIKE}
    ]
    manual_activity_values = [
        float(item.value)
        for item in evidence
        if item.kind is EvidenceKind.ACTIVITY_MINUTES
        and item.value is not None
        and period_start <= item.recorded_on <= period_end
    ]
    manual_activity_minutes = _sum_or_none(manual_activity_values)
    activity_durations = [
        float(item.duration_seconds)
        for item in period_activities
        if item.duration_seconds is not None
    ]
    if manual_activity_minutes is not None:
        activity_durations.append(manual_activity_minutes * 60)
    run_durations = [
        float(item.duration_seconds)
        for item in runs
        if item.duration_seconds is not None
    ]
    run_distances = [
        float(item.distance_meters)
        for item in runs
        if item.distance_meters is not None
    ]
    run_hike_durations = [
        float(item.duration_seconds)
        for item in period_activities
        if item.activity_type in {ActivityType.RUN, ActivityType.HIKE}
        and item.duration_seconds is not None
    ]
    return TrainingMetrics(
        activity_count=len(period_activities),
        runs_completed=len(runs),
        strength_sessions_completed=len(strength),
        swims_completed=len(swims),
        walks_hikes_completed=len(walks_hikes),
        long_run_or_hike_done=any(_is_long_run_or_hike(item) for item in period_activities),
        total_running_distance_meters=_sum_or_none(run_distances),
        total_running_duration_seconds=_sum_or_none(run_durations),
        total_activity_time_seconds=_sum_or_none(activity_durations),
        longest_activity_duration_seconds=max(activity_durations, default=None),
        longest_run_hike_duration_seconds=max(run_hike_durations, default=None),
        manual_activity_minutes=manual_activity_minutes,
    )


def calculate_recovery_metrics(
    observations: list[DailyObservation],
    evidence: list[EvidenceRecord],
    *,
    period_start: date,
    period_end: date,
) -> RecoveryMetrics:
    sleep_by_date = _merged_daily_values(
        observations,
        evidence,
        observation_attribute="sleep_hours",
        manual_kind=EvidenceKind.SLEEP_HOURS,
    )
    stress_by_date = _observation_values(observations, "stress_score")
    body_battery_by_date = _observation_values(observations, "body_battery_avg")
    resting_hr_by_date = _observation_values(observations, "resting_hr")
    latest_observation = max(
        (item for item in observations if item.recorded_on <= period_end),
        key=lambda item: item.recorded_on,
        default=None,
    )
    baseline_start = period_end - timedelta(days=MONTH_DAYS)
    baseline_end = period_end - timedelta(days=1)
    resting_baseline = _average(
        _values_between(resting_hr_by_date, baseline_start, baseline_end)
    )
    latest_resting_hr = (
        _float_or_none(latest_observation.resting_hr) if latest_observation else None
    )
    resting_delta = (
        latest_resting_hr - resting_baseline
        if latest_resting_hr is not None and resting_baseline is not None
        else None
    )
    return RecoveryMetrics(
        average_sleep_hours=_average(
            _values_between(sleep_by_date, period_start, period_end)
        ),
        latest_resting_hr=latest_resting_hr,
        resting_hr_baseline_30d=resting_baseline,
        resting_hr_vs_baseline_30d=resting_delta,
        latest_hrv_value=(
            _float_or_none(latest_observation.hrv_value) if latest_observation else None
        ),
        latest_hrv_status=latest_observation.hrv_status if latest_observation else None,
        average_stress_score=_average(
            _values_between(stress_by_date, period_start, period_end)
        ),
        average_body_battery=_average(
            _values_between(body_battery_by_date, period_start, period_end)
        ),
    )


def calculate_optional_context(
    evidence: list[EvidenceRecord],
    *,
    period_start: date,
    period_end: date,
) -> OptionalContextMetrics:
    period_evidence = [
        item for item in evidence if period_start <= item.recorded_on <= period_end
    ]
    alcohol = _evidence_values(period_evidence, EvidenceKind.ALCOHOL_UNITS)
    calories = _evidence_values(period_evidence, EvidenceKind.CALORIES)
    protein = _evidence_values(period_evidence, EvidenceKind.PROTEIN_G)
    notes = tuple(item for item in period_evidence if item.kind is EvidenceKind.NOTE)
    return OptionalContextMetrics(
        alcohol_units=_sum_or_none(alcohol),
        average_calories=_average(calories),
        average_protein_g=_average(protein),
        notes=notes,
    )


def calculate_metric_coverage(
    observations: list[DailyObservation],
    evidence: list[EvidenceRecord],
    *,
    period_start: date,
    period_end: date,
) -> tuple[MetricCoverage, ...]:
    metric_definitions = (
        ("weight", "Weight", "weight_kg", EvidenceKind.WEIGHT_KG),
        ("body_composition", "Body composition", "body_fat_percent", None),
        ("sleep", "Sleep", "sleep_hours", EvidenceKind.SLEEP_HOURS),
        ("resting_hr", "Resting heart rate", "resting_hr", None),
        ("stress", "Stress", "stress_score", None),
        ("hrv", "HRV", "hrv_value", None),
        ("body_battery", "Body Battery", "body_battery_avg", None),
    )
    coverage = []
    for key, label, attribute, manual_kind in metric_definitions:
        values = _merged_daily_values(
            observations,
            evidence,
            observation_attribute=attribute,
            manual_kind=manual_kind,
        )
        coverage.append(
            MetricCoverage(
                key=key,
                label=label,
                observed_days=len(_values_between(values, period_start, period_end)),
            )
        )
    return tuple(coverage)


def assess_weekly_read(
    weight: WeightMetrics,
    recovery: RecoveryMetrics,
    plan: PlanWeek | None = None,
) -> ReadinessAssessment:
    if weight.current_sample_days < 2 or weight.previous_sample_days < 2:
        return ReadinessAssessment(
            ReadinessState.BUILDING_A_PICTURE,
            "Ready needs two comparable weeks of weight evidence for the current priority.",
            "Complete the history import, then check the weekly averages again.",
        )

    recovery_concerns = _recovery_concerns(recovery)
    weekly_change = weight.weekly_weight_change_kg
    if recovery_concerns:
        return ReadinessAssessment(
            ReadinessState.REVIEW_THE_PLAN,
            "One or more recovery signals crossed the WL comparison thresholds.",
            "Review the evidence before adding training load or cutting fuel further.",
        )
    completion_ratio = (
        plan.completed_sessions / plan.planned_sessions
        if plan is not None and plan.planned_sessions > 0
        else None
    )
    if completion_ratio is not None and completion_ratio < 0.5:
        return ReadinessAssessment(
            ReadinessState.REVIEW_THE_PLAN,
            "Less than half of the active weekly plan matched completed activity.",
            "Review whether the remaining plan still fits before changing course.",
        )
    if (
        weekly_change is not None
        and EXPECTED_LOSS_MIN_KG_PER_WEEK
        <= weekly_change
        <= EXPECTED_LOSS_MAX_KG_PER_WEEK
        and (completion_ratio is None or completion_ratio >= 0.8)
    ):
        return ReadinessAssessment(
            ReadinessState.PROGRESSING,
            "The two weekly weight averages moved in the intended range.",
            "Keep the current approach and use the next completed week to confirm it.",
        )
    if completion_ratio is not None and completion_ratio < 0.8:
        summary = "The goal direction and plan follow-through show mixed signals."
    elif weekly_change is not None and weekly_change < FAST_LOSS_THRESHOLD_KG_PER_WEEK:
        summary = "Weight moved faster than the intended weekly range."
    elif weekly_change is not None and weekly_change > 0:
        summary = "The latest weekly weight average moved upwards."
    else:
        summary = "The weekly weight averages show a mixed direction."
    return ReadinessAssessment(
        ReadinessState.MIXED_SIGNALS,
        summary,
        "Check the evidence and wait for the next weekly average before changing course.",
    )


def _merged_daily_values(
    observations: list[DailyObservation],
    evidence: list[EvidenceRecord],
    *,
    observation_attribute: str,
    manual_kind: EvidenceKind | None,
) -> dict[date, float]:
    values = _observation_values(observations, observation_attribute)
    if manual_kind is None:
        return values
    manual_values: dict[date, float] = {}
    for item in evidence:
        if item.kind is manual_kind and item.value is not None:
            manual_values.setdefault(item.recorded_on, float(item.value))
    values.update(manual_values)
    return values


def _observation_values(
    observations: list[DailyObservation],
    attribute: str,
) -> dict[date, float]:
    values: dict[date, float] = {}
    source_priority = {EvidenceSource.MANUAL: 0, EvidenceSource.GARMIN: 1}
    ordered = sorted(
        observations,
        key=lambda item: (item.recorded_on, source_priority.get(item.source, 9)),
    )
    for item in ordered:
        value = _float_or_none(getattr(item, attribute))
        if value is not None:
            values.setdefault(item.recorded_on, value)
    return values


def _values_between(
    values: dict[date, float],
    start_date: date,
    end_date: date,
) -> list[float]:
    return [
        value
        for item_date, value in values.items()
        if start_date <= item_date <= end_date
    ]


def _evidence_values(
    evidence: list[EvidenceRecord],
    kind: EvidenceKind,
) -> list[float]:
    return [
        float(item.value)
        for item in evidence
        if item.kind is kind and item.value is not None
    ]


def _average(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _sum_or_none(values: list[float]) -> float | None:
    return float(sum(values)) if values else None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_long_run_or_hike(activity: ActivityRecord) -> bool:
    duration = _float_or_none(activity.duration_seconds)
    distance = _float_or_none(activity.distance_meters)
    if activity.activity_type is ActivityType.RUN:
        return bool(
            (duration is not None and duration >= RUN_LONG_DURATION_SECONDS)
            or (distance is not None and distance >= RUN_LONG_DISTANCE_METERS)
        )
    if activity.activity_type is ActivityType.HIKE:
        return duration is not None and duration >= HIKE_LONG_DURATION_SECONDS
    return False


def _recovery_concerns(recovery: RecoveryMetrics) -> tuple[str, ...]:
    concerns = []
    if (
        recovery.average_sleep_hours is not None
        and recovery.average_sleep_hours < LOW_SLEEP_HOURS
    ):
        concerns.append("sleep")
    if (
        recovery.resting_hr_vs_baseline_30d is not None
        and recovery.resting_hr_vs_baseline_30d > RESTING_HR_DELTA_THRESHOLD
    ):
        concerns.append("resting_hr")
    status = (recovery.latest_hrv_status or "").strip().upper()
    if status and status not in POSITIVE_HRV_STATUSES and any(
        hint in status for hint in NEGATIVE_HRV_HINTS
    ):
        concerns.append("hrv")
    return tuple(concerns)
