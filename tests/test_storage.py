from datetime import date

import pytest

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    DailyObservation,
    EvidenceKind,
    EvidenceSource,
    GoalPriority,
)
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    count_evidence_days,
    init_db,
    list_goals,
    list_recent_evidence,
    upsert_activity,
    upsert_daily_observation,
)


def test_reference_goal_stack_is_seeded_idempotently(tmp_path):
    db_path = tmp_path / "ready.sqlite"

    init_db(db_path)
    init_db(db_path)

    with connect(db_path) as conn:
        goals = list_goals(conn)

    assert [goal.title for goal in goals] == [
        "Reach 85 kg",
        "Maintain strength",
        "Build training consistency",
        "Ultra Mirage El Djerid 50 km",
    ]
    assert goals[0].priority is GoalPriority.CURRENT
    assert sum(goal.priority is GoalPriority.CURRENT for goal in goals) == 1


def test_manual_evidence_keeps_optional_context_optional(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        record = add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.WEIGHT_KG,
            value=91.4,
        )
        evidence = list_recent_evidence(conn)

    assert evidence == [record]
    assert record.unit == "kg"
    assert record.note is None


def test_context_note_requires_text(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn, pytest.raises(ValueError, match="needs some text"):
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.NOTE,
        )


def test_evidence_days_count_distinct_dates(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        for value in (91.4, 91.2):
            add_manual_evidence(
                conn,
                recorded_on=date(2026, 9, 3),
                kind=EvidenceKind.WEIGHT_KG,
                value=value,
            )
        assert count_evidence_days(conn) == 1


def test_optional_context_does_not_count_as_goal_evidence(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.ALCOHOL_UNITS,
            value=3,
        )
        assert count_evidence_days(conn) == 0


def test_garmin_observations_and_activities_count_as_evidence(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=date(2026, 9, 2),
                source=EvidenceSource.GARMIN,
                sleep_hours=7.2,
            ),
        )
        upsert_activity(
            conn,
            ActivityRecord(
                source=EvidenceSource.GARMIN,
                external_id="activity-1",
                recorded_on=date(2026, 9, 3),
                activity_type=ActivityType.RUN,
            ),
        )
        assert count_evidence_days(conn) == 2
