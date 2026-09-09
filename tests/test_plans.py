from datetime import date

import pytest

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    EvidenceSource,
    PlanChangeReason,
    PlanChangeType,
    PlanImportItem,
    PlanSource,
    PlannedSessionStatus,
)
from outset_ready.plans import (
    auto_match_planned_sessions,
    build_plan_week,
    create_manual_session,
    import_plan_snapshot,
    list_plan_revisions,
    list_planned_sessions,
    match_planned_session,
    restore_planned_session,
    skip_planned_session,
    update_planned_session,
)
from outset_ready.storage import connect, init_db, upsert_activity


def _activity(external_id: str, recorded_on: date, activity_type: ActivityType):
    return ActivityRecord(
        source=EvidenceSource.GARMIN,
        external_id=external_id,
        recorded_on=recorded_on,
        activity_type=activity_type,
        name=f"Completed {activity_type.value}",
    )


def test_manual_session_preserves_move_skip_and_restore_history(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="Easy run",
            planned_duration_seconds=2_700,
        )
        moved = update_planned_session(
            conn,
            session_id=session.id,
            scheduled_on=date(2026, 9, 10),
            activity_type=ActivityType.RUN,
            title="Easy run",
            planned_duration_seconds=2_700,
            planned_distance_meters=None,
            change_type=PlanChangeType.MOVED,
            reason=PlanChangeReason.SCHEDULE,
            reason_note="Late meeting",
        )
        skipped = skip_planned_session(
            conn,
            session_id=session.id,
            reason=PlanChangeReason.RECOVERY,
        )
        restored = restore_planned_session(conn, session_id=session.id)
        revisions = list_plan_revisions(conn, session_ids=[session.id])

    assert moved.scheduled_on == date(2026, 9, 10)
    assert skipped.status is PlannedSessionStatus.SKIPPED
    assert restored.status is PlannedSessionStatus.PLANNED
    assert [revision.change_type for revision in revisions] == [
        PlanChangeType.RESTORED,
        PlanChangeType.SKIPPED,
        PlanChangeType.MOVED,
        PlanChangeType.ADDED,
    ]
    assert revisions[2].reason is PlanChangeReason.SCHEDULE
    assert revisions[2].reason_note == "Late meeting"


def test_provider_snapshots_update_and_remove_without_duplicates(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)
    first = PlanImportItem(
        external_id="calendar:123",
        scheduled_on=date(2026, 9, 9),
        activity_type=ActivityType.RUN,
        title="Intervals",
        planned_duration_seconds=3_600,
    )

    with connect(db_path) as conn:
        import_plan_snapshot(
            conn,
            source=PlanSource.GARMIN,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            items=[first],
        )
        import_plan_snapshot(
            conn,
            source=PlanSource.GARMIN,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            items=[
                PlanImportItem(
                    external_id="calendar:123",
                    scheduled_on=date(2026, 9, 10),
                    activity_type=ActivityType.RUN,
                    title="Short intervals",
                    planned_duration_seconds=2_700,
                )
            ],
        )
        sessions = list_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        )
        import_plan_snapshot(
            conn,
            source=PlanSource.GARMIN,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            items=[],
        )
        removed = list_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            include_removed=True,
        )
        revisions = list_plan_revisions(conn, session_ids=[removed[0].id])

    assert len(sessions) == 1
    assert sessions[0].scheduled_on == date(2026, 9, 10)
    assert sessions[0].title == "Short intervals"
    assert removed[0].status is PlannedSessionStatus.REMOVED
    assert [revision.change_type for revision in revisions] == [
        PlanChangeType.PROVIDER_REMOVED,
        PlanChangeType.PROVIDER_UPDATED,
        PlanChangeType.IMPORTED,
    ]


def test_owner_override_is_not_overwritten_by_provider_refresh(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)
    provider_item = PlanImportItem(
        external_id="calendar:123",
        scheduled_on=date(2026, 9, 9),
        activity_type=ActivityType.RUN,
        title="Intervals",
    )

    with connect(db_path) as conn:
        import_plan_snapshot(
            conn,
            source=PlanSource.GARMIN,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            items=[provider_item],
        )
        session = list_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        )[0]
        update_planned_session(
            conn,
            session_id=session.id,
            scheduled_on=date(2026, 9, 10),
            activity_type=ActivityType.RUN,
            title="Intervals after work",
            planned_duration_seconds=None,
            planned_distance_meters=None,
            change_type=PlanChangeType.MOVED,
        )
        import_plan_snapshot(
            conn,
            source=PlanSource.GARMIN,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            items=[provider_item],
        )
        saved = list_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        )[0]

    assert saved.scheduled_on == date(2026, 9, 10)
    assert saved.title == "Intervals after work"
    assert saved.manual_override is True


def test_auto_match_requires_exactly_one_same_date_and_type_candidate(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="Easy run",
        )
        upsert_activity(conn, _activity("run-1", date(2026, 9, 9), ActivityType.RUN))
        upsert_activity(conn, _activity("run-2", date(2026, 9, 9), ActivityType.RUN))
        assert auto_match_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        ) == 0

        match_planned_session(
            conn,
            session_id=session.id,
            activity_source=EvidenceSource.GARMIN,
            activity_external_id="run-1",
        )
        week = build_plan_week(
            conn,
            period_start=date(2026, 9, 7),
            period_end=date(2026, 9, 13),
        )

    assert week.completed_sessions == 1
    assert [activity.external_id for activity in week.unmatched_activities] == ["run-2"]


def test_one_activity_cannot_match_two_sessions(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        first = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="First run",
        )
        second = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="Second run",
        )
        upsert_activity(conn, _activity("run-1", date(2026, 9, 9), ActivityType.RUN))
        match_planned_session(
            conn,
            session_id=first.id,
            activity_source=EvidenceSource.GARMIN,
            activity_external_id="run-1",
        )
        with pytest.raises(ValueError, match="already matches"):
            match_planned_session(
                conn,
                session_id=second.id,
                activity_source=EvidenceSource.GARMIN,
                activity_external_id="run-1",
            )


def test_auto_match_does_not_choose_between_two_planned_sessions(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        for title in ("First run", "Second run"):
            create_manual_session(
                conn,
                scheduled_on=date(2026, 9, 9),
                activity_type=ActivityType.RUN,
                title=title,
            )
        upsert_activity(conn, _activity("run-1", date(2026, 9, 9), ActivityType.RUN))

        assert auto_match_planned_sessions(
            conn,
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        ) == 0


def test_matched_session_must_be_unmatched_before_it_is_skipped(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="Easy run",
        )
        upsert_activity(conn, _activity("run-1", date(2026, 9, 9), ActivityType.RUN))
        match_planned_session(
            conn,
            session_id=session.id,
            activity_source=EvidenceSource.GARMIN,
            activity_external_id="run-1",
        )

        with pytest.raises(ValueError, match="Remove the completed activity match"):
            skip_planned_session(conn, session_id=session.id)


def test_manual_match_cannot_consume_activity_from_another_week(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.RUN,
            title="Easy run",
        )
        upsert_activity(conn, _activity("old-run", date(2026, 9, 2), ActivityType.RUN))

        with pytest.raises(ValueError, match="same week"):
            match_planned_session(
                conn,
                session_id=session.id,
                activity_source=EvidenceSource.GARMIN,
                activity_external_id="old-run",
            )


def test_explicit_skip_stays_visible_but_leaves_completion_denominator(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        session = create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 9),
            activity_type=ActivityType.STRENGTH,
            title="Strength",
        )
        skip_planned_session(conn, session_id=session.id)
        week = build_plan_week(
            conn,
            period_start=date(2026, 9, 7),
            period_end=date(2026, 9, 13),
        )

    assert len(week.sessions) == 1
    assert week.skipped_sessions == 1
    assert week.planned_sessions == 0
    assert week.completed_sessions == 0
    assert week.due_unmatched_sessions(through_date=date(2026, 9, 13)) == 0
