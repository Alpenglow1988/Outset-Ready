from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    ConnectorSync,
    ConnectorSyncStatus,
    DailyObservation,
    DEFAULT_UNITS,
    EvidenceKind,
    EvidenceRecord,
    EvidenceSource,
    Goal,
    GoalCategory,
    GoalPriority,
    validate_evidence,
)


REFERENCE_GOALS = (
    Goal(
        id="goal-weight-85",
        title="Reach 85 kg",
        category=GoalCategory.HEALTH,
        priority=GoalPriority.CURRENT,
        sort_order=10,
        target_value=85,
        target_unit="kg",
    ),
    Goal(
        id="goal-strength",
        title="Maintain strength",
        category=GoalCategory.FITNESS,
        priority=GoalPriority.SUPPORTING,
        sort_order=20,
        supports_goal_id="goal-weight-85",
    ),
    Goal(
        id="goal-consistency",
        title="Build training consistency",
        category=GoalCategory.FITNESS,
        priority=GoalPriority.SUPPORTING,
        sort_order=30,
        supports_goal_id="goal-weight-85",
    ),
    Goal(
        id="goal-ultra-mirage",
        title="Ultra Mirage El Djerid 50 km",
        category=GoalCategory.ADVENTURE,
        priority=GoalPriority.FUTURE,
        sort_order=40,
    ),
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS goals (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              category TEXT NOT NULL CHECK (category IN ('health', 'fitness', 'adventure')),
              priority TEXT NOT NULL CHECK (priority IN ('current', 'supporting', 'future')),
              sort_order INTEGER NOT NULL,
              target_value REAL,
              target_unit TEXT,
              target_date TEXT,
              supports_goal_id TEXT REFERENCES goals(id),
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence_records (
              id TEXT PRIMARY KEY,
              recorded_on TEXT NOT NULL,
              source TEXT NOT NULL CHECK (source IN ('garmin', 'manual')),
              kind TEXT NOT NULL,
              value REAL,
              unit TEXT,
              note TEXT,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS evidence_recorded_on_idx
              ON evidence_records(recorded_on DESC, created_at DESC);

            CREATE TABLE IF NOT EXISTS daily_observations (
              recorded_on TEXT NOT NULL,
              source TEXT NOT NULL CHECK (source IN ('garmin', 'manual')),
              weight_kg REAL,
              body_fat_percent REAL,
              fat_mass_kg REAL,
              lean_mass_kg REAL,
              steps INTEGER,
              resting_hr REAL,
              sleep_hours REAL,
              sleep_score REAL,
              stress_score REAL,
              hrv_value REAL,
              hrv_status TEXT,
              body_battery_avg REAL,
              active_calories REAL,
              total_calories REAL,
              source_ref TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (recorded_on, source)
            );

            CREATE TABLE IF NOT EXISTS activities (
              source TEXT NOT NULL CHECK (source IN ('garmin', 'manual')),
              external_id TEXT NOT NULL,
              recorded_on TEXT NOT NULL,
              activity_type TEXT NOT NULL,
              name TEXT,
              duration_seconds REAL,
              distance_meters REAL,
              elevation_gain_meters REAL,
              average_hr REAL,
              calories REAL,
              source_ref TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (source, external_id)
            );

            CREATE INDEX IF NOT EXISTS activities_recorded_on_idx
              ON activities(recorded_on DESC, updated_at DESC);

            CREATE TABLE IF NOT EXISTS connector_syncs (
              id TEXT PRIMARY KEY,
              connector TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              start_date TEXT NOT NULL,
              end_date TEXT NOT NULL,
              daily_records INTEGER NOT NULL DEFAULT 0,
              activity_records INTEGER NOT NULL DEFAULT 0,
              warnings INTEGER NOT NULL DEFAULT 0,
              error_message TEXT
            );
            """
        )
        seed_reference_goals(conn)


def seed_reference_goals(conn: sqlite3.Connection) -> None:
    now = _utc_now()
    with conn:
        for goal in REFERENCE_GOALS:
            conn.execute(
                """
                INSERT OR IGNORE INTO goals (
                  id, title, category, priority, sort_order, target_value,
                  target_unit, target_date, supports_goal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal.id,
                    goal.title,
                    goal.category.value,
                    goal.priority.value,
                    goal.sort_order,
                    goal.target_value,
                    goal.target_unit,
                    goal.target_date.isoformat() if goal.target_date else None,
                    goal.supports_goal_id,
                    now,
                ),
            )


def list_goals(conn: sqlite3.Connection) -> list[Goal]:
    rows = conn.execute(
        """
        SELECT id, title, category, priority, sort_order, target_value,
               target_unit, target_date, supports_goal_id
        FROM goals
        ORDER BY sort_order, created_at
        """
    ).fetchall()
    return [
        Goal(
            id=row["id"],
            title=row["title"],
            category=GoalCategory(row["category"]),
            priority=GoalPriority(row["priority"]),
            sort_order=row["sort_order"],
            target_value=row["target_value"],
            target_unit=row["target_unit"],
            target_date=date.fromisoformat(row["target_date"]) if row["target_date"] else None,
            supports_goal_id=row["supports_goal_id"],
        )
        for row in rows
    ]


def add_manual_evidence(
    conn: sqlite3.Connection,
    *,
    recorded_on: date,
    kind: EvidenceKind,
    value: float | None = None,
    unit: str | None = None,
    note: str | None = None,
) -> EvidenceRecord:
    record = EvidenceRecord(
        id=str(uuid4()),
        recorded_on=recorded_on,
        source=EvidenceSource.MANUAL,
        kind=kind,
        value=value,
        unit=unit or DEFAULT_UNITS.get(kind),
        note=note.strip() if note and note.strip() else None,
    )
    validate_evidence(record)
    with conn:
        conn.execute(
            """
            INSERT INTO evidence_records (
              id, recorded_on, source, kind, value, unit, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.recorded_on.isoformat(),
                record.source.value,
                record.kind.value,
                record.value,
                record.unit,
                record.note,
                _utc_now(),
            ),
        )
    return record


def list_recent_evidence(
    conn: sqlite3.Connection,
    limit: int = 12,
) -> list[EvidenceRecord]:
    rows = conn.execute(
        """
        SELECT id, recorded_on, source, kind, value, unit, note
        FROM evidence_records
        ORDER BY recorded_on DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        EvidenceRecord(
            id=row["id"],
            recorded_on=date.fromisoformat(row["recorded_on"]),
            source=EvidenceSource(row["source"]),
            kind=EvidenceKind(row["kind"]),
            value=row["value"],
            unit=row["unit"],
            note=row["note"],
        )
        for row in rows
    ]


def count_evidence_days(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT recorded_on) AS count
        FROM (
          SELECT recorded_on
          FROM evidence_records
          WHERE kind NOT IN ('alcohol_units', 'calories', 'protein_g', 'note')
          UNION
          SELECT recorded_on
          FROM daily_observations
          WHERE weight_kg IS NOT NULL
             OR body_fat_percent IS NOT NULL
             OR steps IS NOT NULL
             OR resting_hr IS NOT NULL
             OR sleep_hours IS NOT NULL
             OR stress_score IS NOT NULL
             OR hrv_value IS NOT NULL
          UNION
          SELECT recorded_on FROM activities
        )
        """
    ).fetchone()
    return int(row["count"])


def upsert_daily_observation(
    conn: sqlite3.Connection,
    observation: DailyObservation,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO daily_observations (
              recorded_on, source, weight_kg, body_fat_percent, fat_mass_kg,
              lean_mass_kg, steps, resting_hr, sleep_hours, sleep_score,
              stress_score, hrv_value, hrv_status, body_battery_avg,
              active_calories, total_calories, source_ref, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(recorded_on, source) DO UPDATE SET
              weight_kg = excluded.weight_kg,
              body_fat_percent = excluded.body_fat_percent,
              fat_mass_kg = excluded.fat_mass_kg,
              lean_mass_kg = excluded.lean_mass_kg,
              steps = excluded.steps,
              resting_hr = excluded.resting_hr,
              sleep_hours = excluded.sleep_hours,
              sleep_score = excluded.sleep_score,
              stress_score = excluded.stress_score,
              hrv_value = excluded.hrv_value,
              hrv_status = excluded.hrv_status,
              body_battery_avg = excluded.body_battery_avg,
              active_calories = excluded.active_calories,
              total_calories = excluded.total_calories,
              source_ref = excluded.source_ref,
              updated_at = excluded.updated_at
            """,
            (
                observation.recorded_on.isoformat(),
                observation.source.value,
                observation.weight_kg,
                observation.body_fat_percent,
                observation.fat_mass_kg,
                observation.lean_mass_kg,
                observation.steps,
                observation.resting_hr,
                observation.sleep_hours,
                observation.sleep_score,
                observation.stress_score,
                observation.hrv_value,
                observation.hrv_status,
                observation.body_battery_avg,
                observation.active_calories,
                observation.total_calories,
                observation.source_ref,
                _utc_now(),
            ),
        )


def upsert_activity(conn: sqlite3.Connection, activity: ActivityRecord) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO activities (
              source, external_id, recorded_on, activity_type, name,
              duration_seconds, distance_meters, elevation_gain_meters,
              average_hr, calories, source_ref, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
              recorded_on = excluded.recorded_on,
              activity_type = excluded.activity_type,
              name = excluded.name,
              duration_seconds = excluded.duration_seconds,
              distance_meters = excluded.distance_meters,
              elevation_gain_meters = excluded.elevation_gain_meters,
              average_hr = excluded.average_hr,
              calories = excluded.calories,
              source_ref = excluded.source_ref,
              updated_at = excluded.updated_at
            """,
            (
                activity.source.value,
                activity.external_id,
                activity.recorded_on.isoformat(),
                activity.activity_type.value,
                activity.name,
                activity.duration_seconds,
                activity.distance_meters,
                activity.elevation_gain_meters,
                activity.average_hr,
                activity.calories,
                activity.source_ref,
                _utc_now(),
            ),
        )


def list_recent_activities(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[ActivityRecord]:
    rows = conn.execute(
        """
        SELECT source, external_id, recorded_on, activity_type, name,
               duration_seconds, distance_meters, elevation_gain_meters,
               average_hr, calories, source_ref
        FROM activities
        ORDER BY recorded_on DESC, updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        ActivityRecord(
            source=EvidenceSource(row["source"]),
            external_id=row["external_id"],
            recorded_on=date.fromisoformat(row["recorded_on"]),
            activity_type=ActivityType(row["activity_type"]),
            name=row["name"],
            duration_seconds=row["duration_seconds"],
            distance_meters=row["distance_meters"],
            elevation_gain_meters=row["elevation_gain_meters"],
            average_hr=row["average_hr"],
            calories=row["calories"],
            source_ref=row["source_ref"],
        )
        for row in rows
    ]


def start_connector_sync(
    conn: sqlite3.Connection,
    *,
    connector: str,
    start_date: date,
    end_date: date,
) -> str:
    sync_id = str(uuid4())
    with conn:
        conn.execute(
            """
            INSERT INTO connector_syncs (
              id, connector, status, started_at, start_date, end_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sync_id,
                connector,
                ConnectorSyncStatus.RUNNING.value,
                _utc_now(),
                start_date.isoformat(),
                end_date.isoformat(),
            ),
        )
    return sync_id


def finish_connector_sync(
    conn: sqlite3.Connection,
    sync_id: str,
    *,
    status: ConnectorSyncStatus,
    daily_records: int,
    activity_records: int,
    warnings: int,
    error_message: str | None = None,
) -> None:
    if status is ConnectorSyncStatus.RUNNING:
        raise ValueError("A finished connector sync cannot remain running.")
    with conn:
        conn.execute(
            """
            UPDATE connector_syncs
            SET status = ?, finished_at = ?, daily_records = ?,
                activity_records = ?, warnings = ?, error_message = ?
            WHERE id = ?
            """,
            (
                status.value,
                _utc_now(),
                daily_records,
                activity_records,
                warnings,
                error_message,
                sync_id,
            ),
        )


def fetch_latest_connector_sync(
    conn: sqlite3.Connection,
    connector: str,
) -> ConnectorSync | None:
    row = conn.execute(
        """
        SELECT id, connector, status, started_at, finished_at, start_date,
               end_date, daily_records, activity_records, warnings,
               error_message
        FROM connector_syncs
        WHERE connector = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (connector,),
    ).fetchone()
    if row is None:
        return None
    return ConnectorSync(
        id=row["id"],
        connector=row["connector"],
        status=ConnectorSyncStatus(row["status"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        daily_records=row["daily_records"],
        activity_records=row["activity_records"],
        warnings=row["warnings"],
        error_message=row["error_message"],
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
