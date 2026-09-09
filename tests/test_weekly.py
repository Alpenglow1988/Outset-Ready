import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pytest

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    DailyObservation,
    EvidenceKind,
    EvidenceRecord,
    EvidenceSource,
)
from outset_ready.plans import (
    create_manual_session,
    match_planned_session,
    skip_planned_session,
)
from outset_ready.readiness import ReadinessState
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    init_db,
    upsert_activity,
    upsert_daily_observation,
)
from outset_ready.weekly import (
    RecoveryMetrics,
    WeightMetrics,
    assess_weekly_read,
    build_weekly_read,
    calculate_recovery_metrics,
    calculate_weight_metrics,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wl_weekly_parity.json"
PERIOD_START = date(2026, 8, 3)
PERIOD_END = date(2026, 8, 9)


def test_ready_matches_wl_weekly_fixture(tmp_path):
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        _insert_daily_history(conn)
        _insert_activities(conn)
        _insert_manual_context(conn)
        weekly = build_weekly_read(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            target_weight_kg=85,
        )

    assert weekly.period_start.isoformat() == expected["period_start"]
    assert weekly.period_end.isoformat() == expected["period_end"]
    for section in (
        "weight",
        "body_composition",
        "waist",
        "training",
        "recovery",
        "optional_context",
    ):
        actual = asdict(getattr(weekly, section))
        for key, value in expected[section].items():
            if isinstance(value, float):
                assert actual[key] == pytest.approx(value)
            else:
                assert actual[key] == value
    assert weekly.weight.kg_remaining_to_target_weight == 6.0
    assert weekly.weight.current_sample_days == 7
    assert weekly.weight.previous_sample_days == 7
    assert weekly.assessment.state is ReadinessState.PROGRESSING
    assert {item.key: item.observed_days for item in weekly.coverage} == {
        "weight": 7,
        "body_composition": 1,
        "sleep": 7,
        "resting_hr": 7,
        "stress": 7,
        "hrv": 1,
        "body_battery": 7,
    }
    assert [item.external_id for item in weekly.activities][:2] == [
        "walk-1",
        "swim-1",
    ]


def test_manual_weight_and_sleep_override_same_day_garmin_values():
    observations = [
        DailyObservation(
            recorded_on=PERIOD_END,
            source=EvidenceSource.GARMIN,
            weight_kg=92,
            sleep_hours=5,
        )
    ]
    evidence = [
        EvidenceRecord(
            id="weight",
            recorded_on=PERIOD_END,
            source=EvidenceSource.MANUAL,
            kind=EvidenceKind.WEIGHT_KG,
            value=91,
        ),
        EvidenceRecord(
            id="sleep",
            recorded_on=PERIOD_END,
            source=EvidenceSource.MANUAL,
            kind=EvidenceKind.SLEEP_HOURS,
            value=7.5,
        ),
    ]

    weight = calculate_weight_metrics(
        observations,
        evidence,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    recovery = calculate_recovery_metrics(
        observations,
        evidence,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )

    assert weight.latest_weight_kg == 91
    assert recovery.average_sleep_hours == 7.5


def test_missing_optional_context_stays_unknown(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        weekly = build_weekly_read(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    assert weekly.optional_context.alcohol_units is None
    assert weekly.optional_context.average_calories is None
    assert weekly.optional_context.average_protein_g is None
    assert weekly.assessment.state is ReadinessState.BUILDING_A_PICTURE


def test_recovery_concern_uses_neutral_review_state():
    weight = WeightMetrics(
        latest_weight_kg=91,
        latest_weight_date=PERIOD_END,
        seven_day_average_weight_kg=91.3,
        previous_seven_day_average_weight_kg=92,
        weekly_weight_change_kg=-0.7,
        thirty_day_weight_change_kg=-2,
        kg_remaining_to_target_weight=6,
        current_sample_days=7,
        previous_sample_days=7,
    )
    recovery = RecoveryMetrics(
        average_sleep_hours=6.0,
        latest_resting_hr=50,
        resting_hr_baseline_30d=50,
        resting_hr_vs_baseline_30d=0,
        latest_hrv_value=45,
        latest_hrv_status="balanced",
        average_stress_score=30,
        average_body_battery=60,
    )

    assessment = assess_weekly_read(weight, recovery)

    assert assessment.state is ReadinessState.REVIEW_THE_PLAN
    assert "recovery" in assessment.summary.lower()


def test_completed_plan_with_explicit_skip_can_still_be_progressing(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        _insert_daily_history(conn)
        _insert_activities(conn)
        run = create_manual_session(
            conn,
            scheduled_on=PERIOD_START,
            activity_type=ActivityType.RUN,
            title="Easy run",
        )
        strength = create_manual_session(
            conn,
            scheduled_on=PERIOD_START + timedelta(days=1),
            activity_type=ActivityType.STRENGTH,
            title="Strength",
        )
        match_planned_session(
            conn,
            session_id=run.id,
            activity_source=EvidenceSource.GARMIN,
            activity_external_id="run-1",
        )
        skip_planned_session(conn, session_id=strength.id)
        weekly = build_weekly_read(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    assert weekly.plan.completed_sessions == 1
    assert weekly.plan.planned_sessions == 1
    assert weekly.plan.skipped_sessions == 1
    assert weekly.assessment.state is ReadinessState.PROGRESSING


def test_low_plan_follow_through_uses_review_state(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        _insert_daily_history(conn)
        for offset, activity_type in enumerate(
            (ActivityType.RUN, ActivityType.STRENGTH, ActivityType.RUN)
        ):
            create_manual_session(
                conn,
                scheduled_on=PERIOD_START + timedelta(days=offset),
                activity_type=activity_type,
                title=f"Planned {activity_type.value}",
            )
        weekly = build_weekly_read(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    assert weekly.plan.completed_sessions == 0
    assert weekly.plan.planned_sessions == 3
    assert weekly.assessment.state is ReadinessState.REVIEW_THE_PLAN
    assert "weekly plan" in weekly.assessment.summary.lower()


def _insert_daily_history(conn) -> None:
    previous_weights = {
        PERIOD_START - timedelta(days=offset): 92.0
        for offset in range(1, 8)
    }
    current_weights = {
        PERIOD_START + timedelta(days=offset): 91.6 - (offset * 0.1)
        for offset in range(7)
    }
    cursor = PERIOD_END - timedelta(days=30)
    while cursor <= PERIOD_END:
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=cursor,
                source=EvidenceSource.GARMIN,
                weight_kg=(
                    93.0
                    if cursor == PERIOD_END - timedelta(days=30)
                    else previous_weights.get(cursor, current_weights.get(cursor))
                ),
                body_fat_percent=29.0 if cursor == PERIOD_END else None,
                fat_mass_kg=26.4 if cursor == PERIOD_END else None,
                lean_mass_kg=64.6 if cursor == PERIOD_END else None,
                resting_hr=52 if cursor == PERIOD_END else 50,
                sleep_hours=7 if PERIOD_START <= cursor <= PERIOD_END else None,
                stress_score=30 if PERIOD_START <= cursor <= PERIOD_END else None,
                hrv_value=45 if cursor == PERIOD_END else None,
                hrv_status="balanced" if cursor == PERIOD_END else None,
                body_battery_avg=60 if PERIOD_START <= cursor <= PERIOD_END else None,
            ),
        )
        cursor += timedelta(days=1)


def _insert_activities(conn) -> None:
    activities = (
        ("run-1", 0, ActivityType.RUN, 3000, 6000),
        ("run-2", 2, ActivityType.RUN, 5400, 12000),
        ("strength-1", 3, ActivityType.STRENGTH, 2700, None),
        ("strength-2", 4, ActivityType.STRENGTH, 2700, None),
        ("swim-1", 5, ActivityType.SWIM, 1800, None),
        ("walk-1", 6, ActivityType.WALK, 1800, None),
    )
    for external_id, offset, activity_type, duration, distance in activities:
        upsert_activity(
            conn,
            ActivityRecord(
                source=EvidenceSource.GARMIN,
                external_id=external_id,
                recorded_on=PERIOD_START + timedelta(days=offset),
                activity_type=activity_type,
                duration_seconds=duration,
                distance_meters=distance,
            ),
        )


def _insert_manual_context(conn) -> None:
    entries = (
        (date(2026, 7, 15), EvidenceKind.WAIST_CM, 102, None),
        (PERIOD_END, EvidenceKind.WAIST_CM, 101, None),
        (PERIOD_END, EvidenceKind.ALCOHOL_UNITS, 3.3, None),
        (PERIOD_START, EvidenceKind.CALORIES, 2000, None),
        (PERIOD_END, EvidenceKind.CALORIES, 2200, None),
        (PERIOD_START, EvidenceKind.PROTEIN_G, 100, None),
        (PERIOD_END, EvidenceKind.PROTEIN_G, 120, None),
        (PERIOD_END, EvidenceKind.NOTE, None, "Good protein, tired legs"),
    )
    for recorded_on, kind, value, note in entries:
        add_manual_evidence(
            conn,
            recorded_on=recorded_on,
            kind=kind,
            value=value,
            note=note,
        )
