from __future__ import annotations

import os
from datetime import date

import pytest

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    DailyObservation,
    EvidenceKind,
    EvidenceSource,
)
from outset_ready.plans import build_plan_week, create_manual_session
from outset_ready.storage import (
    add_manual_evidence,
    claim_weekly_review_interpretation,
    complete_weekly_review_interpretation,
    connect,
    count_evidence_days,
    database_is_ready,
    ensure_owner,
    fetch_weekly_review_interpretation,
    finalise_weekly_review,
    init_db,
    list_goals,
    list_activities_between,
    list_daily_observations_between,
    list_evidence_between,
    list_recent_activities,
    list_recent_evidence,
    save_weekly_review_draft,
    upsert_activity,
    upsert_daily_observation,
)


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="Postgres contract runs in CI.")
def test_postgres_implements_the_ready_storage_contract():
    assert POSTGRES_URL is not None
    init_db(POSTGRES_URL, owner_email="ian@example.com")
    with connect(POSTGRES_URL) as conn:
        with conn.transaction():
            for table in (
                "weekly_review_interpretations",
                "weekly_reviews",
                "planned_activity_matches",
                "planned_session_revisions",
                "planned_sessions",
                "plan_snapshot_sessions",
                "plan_snapshots",
                "connector_connections",
                "connector_syncs",
                "activities",
                "daily_observations",
                "evidence_records",
                "goal_revisions",
                "goals",
                "users",
            ):
                conn.execute(f"DELETE FROM {table}")

    init_db(POSTGRES_URL, owner_email="ian@example.com")
    with connect(POSTGRES_URL) as conn:
        with conn.transaction():
            ensure_owner(conn, user_id="other", email="other@example.com")
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.WEIGHT_KG,
            value=91.4,
        )
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=date(2026, 9, 2),
                source=EvidenceSource.GARMIN,
                sleep_hours=7.2,
            ),
        )
        activity = ActivityRecord(
            source=EvidenceSource.GARMIN,
            external_id="activity-1",
            recorded_on=date(2026, 9, 3),
            activity_type=ActivityType.RUN,
        )
        upsert_activity(conn, activity)
        upsert_activity(conn, activity)
        create_manual_session(
            conn,
            scheduled_on=date(2026, 9, 3),
            activity_type=ActivityType.RUN,
            title="Easy run",
        )
        review = save_weekly_review_draft(
            conn,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 7),
            evidence_fingerprint="a" * 64,
            snapshot_json='{"snapshot_version":1}',
        )
        finalise_weekly_review(
            conn,
            review_id=review.id,
            expected_fingerprint=review.evidence_fingerprint,
        )
        assert claim_weekly_review_interpretation(
            conn,
            review_id=review.id,
            provider="test",
            model="test-model",
            prompt_version="v1",
        )
        complete_weekly_review_interpretation(
            conn,
            review_id=review.id,
            what_went_well="Evidence recorded.",
            main_risk="More evidence needed.",
            one_adjustment="Keep collecting evidence.",
            encouragement="Review next week.",
            provider_response_id="test-response",
        )

        assert len(list_goals(conn)) == 4
        assert len(list_recent_evidence(conn)) == 1
        assert len(list_recent_activities(conn)) == 1
        assert count_evidence_days(conn) == 2
        assert list_recent_evidence(conn, user_id="other") == []
        assert len(
            list_evidence_between(
                conn,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 3),
            )
        ) == 1
        assert len(
            list_daily_observations_between(
                conn,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 3),
            )
        ) == 1
        assert len(
            list_activities_between(
                conn,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 3),
            )
        ) == 1
        assert build_plan_week(
            conn,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 7),
        ).planned_sessions == 1
        assert fetch_weekly_review_interpretation(
            conn,
            review_id=review.id,
        ).what_went_well == "Evidence recorded."
    assert database_is_ready(POSTGRES_URL)
