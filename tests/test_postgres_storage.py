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
from outset_ready.storage import (
    add_manual_evidence,
    connect,
    count_evidence_days,
    database_is_ready,
    ensure_owner,
    init_db,
    list_goals,
    list_activities_between,
    list_daily_observations_between,
    list_evidence_between,
    list_recent_activities,
    list_recent_evidence,
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
    assert database_is_ready(POSTGRES_URL)
