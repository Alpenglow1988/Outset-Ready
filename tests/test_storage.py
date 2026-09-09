from datetime import date
from datetime import UTC, datetime, timedelta

import pytest

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    ConnectorSyncStatus,
    DailyObservation,
    EvidenceKind,
    EvidenceSource,
    GoalPriority,
    ConnectorConnectionStatus,
)
from outset_ready.storage import (
    ConnectorSyncAlreadyRunning,
    add_manual_evidence,
    connect,
    count_evidence_days,
    delete_connector_connection,
    ensure_owner,
    fetch_connector_connection,
    finish_connector_sync,
    init_db,
    list_activities_between,
    list_connector_syncs,
    list_daily_observations_between,
    list_evidence_between,
    list_latest_evidence_of_kind,
    list_goals,
    list_recent_evidence,
    load_connector_credentials,
    mark_connector_connected,
    mark_connector_reconnect_required,
    save_connector_credentials,
    seed_reference_goals,
    start_connector_sync,
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


def test_period_queries_keep_owner_data_scoped_and_ordered(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 2),
            kind=EvidenceKind.WAIST_CM,
            value=102,
        )
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.WAIST_CM,
            value=101,
        )
        add_manual_evidence(
            conn,
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.WAIST_CM,
            value=100.5,
        )
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=date(2026, 9, 3),
                source=EvidenceSource.GARMIN,
                weight_kg=91.4,
            ),
        )
        upsert_activity(
            conn,
            ActivityRecord(
                source=EvidenceSource.GARMIN,
                external_id="run-1",
                recorded_on=date(2026, 9, 3),
                activity_type=ActivityType.RUN,
            ),
        )

        evidence = list_evidence_between(
            conn,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )
        waist = list_latest_evidence_of_kind(
            conn,
            kind=EvidenceKind.WAIST_CM,
            through_date=date(2026, 9, 3),
        )
        observations = list_daily_observations_between(
            conn,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )
        activities = list_activities_between(
            conn,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )

    assert [record.value for record in evidence] == [102, 100.5, 101]
    assert [record.value for record in waist] == [100.5, 102]
    assert observations[0].weight_kg == 91.4
    assert activities[0].external_id == "run-1"


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


def test_user_owned_records_are_isolated(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path, owner_email="first@example.com")

    with connect(db_path) as conn:
        with conn:
            ensure_owner(conn, user_id="second", email="second@example.com")
            seed_reference_goals(conn, user_id="second")
        add_manual_evidence(
            conn,
            user_id="second",
            recorded_on=date(2026, 9, 3),
            kind=EvidenceKind.WEIGHT_KG,
            value=80,
        )

        assert list_recent_evidence(conn, user_id="owner") == []
        assert len(list_recent_evidence(conn, user_id="second")) == 1
        assert len(list_goals(conn, user_id="owner")) == 4
        assert len(list_goals(conn, user_id="second")) == 4


def test_existing_single_owner_sqlite_data_is_migrated(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE goals (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              category TEXT NOT NULL,
              priority TEXT NOT NULL,
              sort_order INTEGER NOT NULL,
              target_value REAL,
              target_unit TEXT,
              target_date TEXT,
              supports_goal_id TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE evidence_records (
              id TEXT PRIMARY KEY, recorded_on TEXT NOT NULL, source TEXT NOT NULL,
              kind TEXT NOT NULL, value REAL, unit TEXT, note TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE daily_observations (
              recorded_on TEXT NOT NULL, source TEXT NOT NULL, weight_kg REAL,
              body_fat_percent REAL, fat_mass_kg REAL, lean_mass_kg REAL, steps INTEGER,
              resting_hr REAL, sleep_hours REAL, sleep_score REAL, stress_score REAL,
              hrv_value REAL, hrv_status TEXT, body_battery_avg REAL,
              active_calories REAL, total_calories REAL, source_ref TEXT,
              updated_at TEXT NOT NULL, PRIMARY KEY (recorded_on, source)
            );
            CREATE TABLE activities (
              source TEXT NOT NULL, external_id TEXT NOT NULL, recorded_on TEXT NOT NULL,
              activity_type TEXT NOT NULL, name TEXT, duration_seconds REAL,
              distance_meters REAL, elevation_gain_meters REAL, average_hr REAL,
              calories REAL, source_ref TEXT, updated_at TEXT NOT NULL,
              PRIMARY KEY (source, external_id)
            );
            CREATE TABLE connector_syncs (
              id TEXT PRIMARY KEY, connector TEXT NOT NULL, status TEXT NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT, start_date TEXT NOT NULL,
              end_date TEXT NOT NULL, daily_records INTEGER NOT NULL DEFAULT 0,
              activity_records INTEGER NOT NULL DEFAULT 0, warnings INTEGER NOT NULL DEFAULT 0,
              error_message TEXT
            );
            INSERT INTO evidence_records VALUES (
              'legacy', '2026-09-03', 'manual', 'weight_kg', 91.4, 'kg', NULL, 'now'
            );
            """
        )

    init_db(db_path, owner_email="ian@example.com")

    with connect(db_path) as conn:
        evidence = list_recent_evidence(conn, user_id="owner")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(evidence_records)")}
        upsert_daily_observation(
            conn,
            DailyObservation(
                recorded_on=date(2026, 9, 3),
                source=EvidenceSource.GARMIN,
                sleep_hours=7.5,
            ),
        )
        upsert_activity(
            conn,
            ActivityRecord(
                source=EvidenceSource.GARMIN,
                external_id="legacy-compatible",
                recorded_on=date(2026, 9, 3),
                activity_type=ActivityType.RUN,
            ),
        )
    assert evidence[0].value == 91.4
    assert "user_id" in columns


def test_connector_credentials_are_owner_scoped_and_statused(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path, owner_email="first@example.com")

    with connect(db_path) as conn:
        with conn:
            ensure_owner(conn, user_id="second", email="second@example.com")
        save_connector_credentials(
            conn,
            connector="garmin",
            encrypted_credentials="ciphertext",
            user_id="second",
        )
        assert load_connector_credentials(conn, connector="garmin") is None
        assert (
            load_connector_credentials(conn, connector="garmin", user_id="second")
            == "ciphertext"
        )
        connection = fetch_connector_connection(
            conn,
            connector="garmin",
            user_id="second",
        )
        assert connection is not None
        assert connection.status is ConnectorConnectionStatus.TOKEN_SAVED

        mark_connector_connected(conn, connector="garmin", user_id="second")
        connected = fetch_connector_connection(
            conn,
            connector="garmin",
            user_id="second",
        )
        assert connected is not None
        assert connected.status is ConnectorConnectionStatus.CONNECTED
        assert connected.connected_at is not None

        mark_connector_reconnect_required(
            conn,
            connector="garmin",
            user_id="second",
        )
        reconnect = fetch_connector_connection(
            conn,
            connector="garmin",
            user_id="second",
        )
        assert reconnect is not None
        assert reconnect.status is ConnectorConnectionStatus.RECONNECT_REQUIRED

        delete_connector_connection(conn, connector="garmin", user_id="second")
        assert (
            fetch_connector_connection(conn, connector="garmin", user_id="second")
            is None
        )


def test_connector_allows_one_running_sync_and_recovers_stale_record(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        first_id = start_connector_sync(
            conn,
            connector="garmin",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )
        with pytest.raises(ConnectorSyncAlreadyRunning):
            start_connector_sync(
                conn,
                connector="garmin",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 7),
            )

        stale_time = (datetime.now(UTC) - timedelta(minutes=6)).isoformat()
        conn.execute(
            "UPDATE connector_syncs SET started_at = ? WHERE id = ?",
            (stale_time, first_id),
        )
        second_id = start_connector_sync(
            conn,
            connector="garmin",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )

        first = conn.execute(
            "SELECT status, error_message FROM connector_syncs WHERE id = ?",
            (first_id,),
        ).fetchone()
    assert second_id != first_id
    assert first["status"] == "failed"
    assert first["error_message"] == "Previous Garmin sync did not finish."


def test_connector_sync_history_returns_completed_windows(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        sync_id = start_connector_sync(
            conn,
            connector="garmin",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )
        finish_connector_sync(
            conn,
            sync_id,
            status=ConnectorSyncStatus.COMPLETED_WITH_WARNINGS,
            daily_records=7,
            activity_records=3,
            warnings=1,
        )
        syncs = list_connector_syncs(conn, "garmin")

    assert len(syncs) == 1
    assert syncs[0].start_date == date(2026, 9, 1)
    assert syncs[0].end_date == date(2026, 9, 7)
    assert syncs[0].warnings == 1
