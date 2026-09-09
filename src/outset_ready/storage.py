from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    ConnectorConnection,
    ConnectorConnectionStatus,
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


DatabaseTarget: TypeAlias = str | Path
DEFAULT_OWNER_ID = "owner"
CONNECTOR_SYNC_STALE_AFTER = timedelta(minutes=5)


class ConnectorSyncAlreadyRunning(RuntimeError):
    """Raised when one owner starts the same connector twice."""

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

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      email TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
      user_id TEXT NOT NULL REFERENCES users(id),
      id TEXT NOT NULL,
      title TEXT NOT NULL,
      category TEXT NOT NULL CHECK (category IN ('health', 'fitness', 'adventure')),
      priority TEXT NOT NULL CHECK (priority IN ('current', 'supporting', 'future')),
      sort_order INTEGER NOT NULL,
      target_value REAL,
      target_unit TEXT,
      target_date TEXT,
      supports_goal_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      archived_at TEXT,
      PRIMARY KEY (user_id, id),
      FOREIGN KEY (user_id, supports_goal_id) REFERENCES goals(user_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goal_revisions (
      user_id TEXT NOT NULL REFERENCES users(id),
      id TEXT PRIMARY KEY,
      goal_id TEXT NOT NULL,
      title TEXT NOT NULL,
      category TEXT NOT NULL CHECK (category IN ('health', 'fitness', 'adventure')),
      priority TEXT NOT NULL CHECK (priority IN ('current', 'supporting', 'future')),
      sort_order INTEGER NOT NULL,
      target_value REAL,
      target_unit TEXT,
      target_date TEXT,
      supports_goal_id TEXT,
      archived_at TEXT,
      effective_from TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_records (
      user_id TEXT NOT NULL REFERENCES users(id),
      id TEXT PRIMARY KEY,
      recorded_on TEXT NOT NULL,
      source TEXT NOT NULL CHECK (source IN ('garmin', 'manual')),
      kind TEXT NOT NULL,
      value REAL,
      unit TEXT,
      note TEXT,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_observations (
      user_id TEXT NOT NULL REFERENCES users(id),
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
      PRIMARY KEY (user_id, recorded_on, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activities (
      user_id TEXT NOT NULL REFERENCES users(id),
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
      PRIMARY KEY (user_id, source, external_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connector_syncs (
      user_id TEXT NOT NULL REFERENCES users(id),
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connector_connections (
      user_id TEXT NOT NULL REFERENCES users(id),
      connector TEXT NOT NULL,
      encrypted_credentials TEXT NOT NULL,
      status TEXT NOT NULL CHECK (
        status IN ('token_saved', 'connected', 'reconnect_required')
      ),
      connected_at TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (user_id, connector)
    )
    """,
)

INDEX_STATEMENTS = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS goals_one_current_active_idx
      ON goals(user_id)
      WHERE priority = 'current' AND archived_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS goal_revisions_user_effective_idx
      ON goal_revisions(user_id, goal_id, effective_from DESC, created_at DESC)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS daily_observations_user_source_idx
      ON daily_observations(user_id, recorded_on, source)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS activities_user_source_idx
      ON activities(user_id, source, external_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS evidence_user_recorded_on_idx
      ON evidence_records(user_id, recorded_on DESC, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS activities_user_recorded_on_idx
      ON activities(user_id, recorded_on DESC, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS connector_syncs_user_started_at_idx
      ON connector_syncs(user_id, connector, started_at DESC)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS connector_syncs_one_running_idx
      ON connector_syncs(user_id, connector)
      WHERE status = 'running'
    """,
)


def is_postgres_target(target: DatabaseTarget) -> bool:
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


def connect(target: DatabaseTarget):
    if is_postgres_target(target):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Postgres support requires psycopg.") from exc
        return psycopg.connect(str(target), row_factory=dict_row)

    db_path = Path(target).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(
    target: DatabaseTarget,
    *,
    owner_email: str = "owner@local",
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    with connect(target) as conn:
        with _transaction(conn):
            for statement in SCHEMA_STATEMENTS:
                _execute(conn, statement)
            if isinstance(conn, sqlite3.Connection):
                _migrate_legacy_sqlite_tables(conn)
            _migrate_goal_columns(conn)
            for statement in INDEX_STATEMENTS:
                _execute(conn, statement)
            ensure_owner(conn, user_id=user_id, email=owner_email)
            seed_reference_goals(conn, user_id=user_id)
            _ensure_initial_goal_revisions(conn, user_id=user_id)


def ensure_owner(conn, *, user_id: str, email: str) -> None:
    _execute(
        conn,
        """
        INSERT INTO users (id, email, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET email = excluded.email
        """,
        (user_id, email, _utc_now()),
    )


def seed_reference_goals(conn, *, user_id: str = DEFAULT_OWNER_ID) -> None:
    now = _utc_now()
    for goal in REFERENCE_GOALS:
        _execute(
            conn,
            """
            INSERT INTO goals (
              user_id, id, title, category, priority, sort_order, target_value,
              target_unit, target_date, supports_goal_id, created_at, updated_at,
              archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                user_id,
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
                now,
                None,
            ),
        )


def list_goals(
    conn,
    *,
    user_id: str = DEFAULT_OWNER_ID,
    include_archived: bool = False,
) -> list[Goal]:
    archived_filter = "" if include_archived else "AND archived_at IS NULL"
    rows = _execute(
        conn,
        f"""
        SELECT id, title, category, priority, sort_order, target_value,
               target_unit, target_date, supports_goal_id, archived_at
        FROM goals
        WHERE user_id = ? {archived_filter}
        ORDER BY sort_order, created_at
        """,
        (user_id,),
    ).fetchall()
    return [_goal_from_row(row) for row in rows]


def list_goals_as_of(
    conn,
    *,
    effective_at: datetime,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[Goal]:
    rows = _execute(
        conn,
        """
        SELECT goal_id AS id, title, category, priority, sort_order, target_value,
               target_unit, target_date, supports_goal_id, archived_at
        FROM (
          SELECT id AS revision_id, goal_id, title, category, priority, sort_order,
                 target_value, target_unit, target_date, supports_goal_id,
                 archived_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY goal_id
                   ORDER BY effective_from DESC, created_at DESC, id DESC
                 ) AS revision_rank
          FROM goal_revisions
          WHERE user_id = ? AND effective_from <= ?
        ) AS ranked_revisions
        WHERE revision_rank = 1 AND archived_at IS NULL
        ORDER BY sort_order, goal_id
        """,
        (user_id, effective_at.isoformat()),
    ).fetchall()
    return [_goal_from_row(row) for row in rows]


def create_goal(
    conn,
    *,
    title: str,
    category: GoalCategory,
    priority: GoalPriority,
    target_value: float | None = None,
    target_unit: str | None = None,
    target_date: date | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> Goal:
    clean_title, clean_unit = _validate_goal_fields(
        title=title,
        target_value=target_value,
        target_unit=target_unit,
    )
    goal_id = f"goal-{uuid4()}"
    now = _utc_now()
    with _transaction(conn):
        if priority is GoalPriority.CURRENT:
            _demote_current_goals(conn, user_id=user_id, except_goal_id=None, now=now)
        row = _execute(
            conn,
            "SELECT COALESCE(MAX(sort_order), 0) AS maximum FROM goals WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        sort_order = int(row["maximum"]) + 10
        _execute(
            conn,
            """
            INSERT INTO goals (
              user_id, id, title, category, priority, sort_order, target_value,
              target_unit, target_date, supports_goal_id, created_at, updated_at,
              archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                goal_id,
                clean_title,
                category.value,
                priority.value,
                sort_order,
                target_value,
                clean_unit,
                target_date.isoformat() if target_date else None,
                None,
                now,
                now,
                None,
            ),
        )
        _record_goal_revision(conn, user_id=user_id, goal_id=goal_id, effective_from=now)
    return fetch_goal(conn, goal_id=goal_id, user_id=user_id)


def update_goal(
    conn,
    *,
    goal_id: str,
    title: str,
    category: GoalCategory,
    priority: GoalPriority,
    target_value: float | None = None,
    target_unit: str | None = None,
    target_date: date | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> Goal:
    clean_title, clean_unit = _validate_goal_fields(
        title=title,
        target_value=target_value,
        target_unit=target_unit,
    )
    now = _utc_now()
    with _transaction(conn):
        existing_goal = fetch_goal(conn, goal_id=goal_id, user_id=user_id)
        if (
            existing_goal.priority is GoalPriority.CURRENT
            and priority is not GoalPriority.CURRENT
        ):
            raise ValueError(
                "Choose a different current goal before changing this priority."
            )
        if priority is GoalPriority.CURRENT:
            _demote_current_goals(
                conn,
                user_id=user_id,
                except_goal_id=goal_id,
                now=now,
            )
        cursor = _execute(
            conn,
            """
            UPDATE goals
            SET title = ?, category = ?, priority = ?, target_value = ?,
                target_unit = ?, target_date = ?, updated_at = ?
            WHERE user_id = ? AND id = ? AND archived_at IS NULL
            """,
            (
                clean_title,
                category.value,
                priority.value,
                target_value,
                clean_unit,
                target_date.isoformat() if target_date else None,
                now,
                user_id,
                goal_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError("That active goal does not exist.")
        _record_goal_revision(conn, user_id=user_id, goal_id=goal_id, effective_from=now)
    return fetch_goal(conn, goal_id=goal_id, user_id=user_id)


def archive_goal(
    conn,
    *,
    goal_id: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> Goal:
    now = _utc_now()
    with _transaction(conn):
        goal = fetch_goal(conn, goal_id=goal_id, user_id=user_id)
        if goal.priority is GoalPriority.CURRENT:
            raise ValueError("Choose a different current goal before archiving this one.")
        cursor = _execute(
            conn,
            """
            UPDATE goals
            SET archived_at = ?, updated_at = ?
            WHERE user_id = ? AND id = ? AND archived_at IS NULL
            """,
            (now, now, user_id, goal_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("That active goal does not exist.")
        _record_goal_revision(conn, user_id=user_id, goal_id=goal_id, effective_from=now)
    return fetch_goal(conn, goal_id=goal_id, user_id=user_id, include_archived=True)


def fetch_goal(
    conn,
    *,
    goal_id: str,
    user_id: str = DEFAULT_OWNER_ID,
    include_archived: bool = False,
) -> Goal:
    archived_filter = "" if include_archived else "AND archived_at IS NULL"
    row = _execute(
        conn,
        f"""
        SELECT id, title, category, priority, sort_order, target_value,
               target_unit, target_date, supports_goal_id, archived_at
        FROM goals
        WHERE user_id = ? AND id = ? {archived_filter}
        """,
        (user_id, goal_id),
    ).fetchone()
    if row is None:
        raise LookupError("That goal does not exist.")
    return _goal_from_row(row)


def add_manual_evidence(
    conn,
    *,
    recorded_on: date,
    kind: EvidenceKind,
    value: float | None = None,
    unit: str | None = None,
    note: str | None = None,
    user_id: str = DEFAULT_OWNER_ID,
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
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO evidence_records (
              user_id, id, recorded_on, source, kind, value, unit, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
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
    conn,
    limit: int = 12,
    *,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[EvidenceRecord]:
    rows = _execute(
        conn,
        """
        SELECT id, recorded_on, source, kind, value, unit, note
        FROM evidence_records
        WHERE user_id = ?
        ORDER BY recorded_on DESC, created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
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


def list_evidence_between(
    conn,
    *,
    start_date: date,
    end_date: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[EvidenceRecord]:
    rows = _execute(
        conn,
        """
        SELECT id, recorded_on, source, kind, value, unit, note
        FROM evidence_records
        WHERE user_id = ? AND recorded_on BETWEEN ? AND ?
        ORDER BY recorded_on ASC, created_at DESC
        """,
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return [_evidence_record_from_row(row) for row in rows]


def list_latest_evidence_of_kind(
    conn,
    *,
    kind: EvidenceKind,
    through_date: date,
    limit: int = 2,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[EvidenceRecord]:
    rows = _execute(
        conn,
        """
        SELECT id, recorded_on, source, kind, value, unit, note
        FROM (
            SELECT id, recorded_on, source, kind, value, unit, note, created_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY recorded_on
                       ORDER BY created_at DESC, id DESC
                   ) AS date_rank
            FROM evidence_records
            WHERE user_id = ? AND kind = ? AND recorded_on <= ?
        ) AS ranked_evidence
        WHERE date_rank = 1
        ORDER BY recorded_on DESC, created_at DESC
        LIMIT ?
        """,
        (user_id, kind.value, through_date.isoformat(), limit),
    ).fetchall()
    return [_evidence_record_from_row(row) for row in rows]


def count_evidence_days(conn, *, user_id: str = DEFAULT_OWNER_ID) -> int:
    row = _execute(
        conn,
        """
        SELECT COUNT(DISTINCT recorded_on) AS count
        FROM (
          SELECT recorded_on
          FROM evidence_records
          WHERE user_id = ?
            AND kind NOT IN ('alcohol_units', 'calories', 'protein_g', 'note')
          UNION
          SELECT recorded_on
          FROM daily_observations
          WHERE user_id = ?
            AND (weight_kg IS NOT NULL
             OR body_fat_percent IS NOT NULL
             OR steps IS NOT NULL
             OR resting_hr IS NOT NULL
             OR sleep_hours IS NOT NULL
             OR stress_score IS NOT NULL
             OR hrv_value IS NOT NULL)
          UNION
          SELECT recorded_on FROM activities WHERE user_id = ?
        ) AS evidence_dates
        """,
        (user_id, user_id, user_id),
    ).fetchone()
    return int(row["count"])


def upsert_daily_observation(
    conn,
    observation: DailyObservation,
    *,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO daily_observations (
              user_id, recorded_on, source, weight_kg, body_fat_percent,
              fat_mass_kg, lean_mass_kg, steps, resting_hr, sleep_hours,
              sleep_score, stress_score, hrv_value, hrv_status, body_battery_avg,
              active_calories, total_calories, source_ref, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, recorded_on, source) DO UPDATE SET
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
                user_id,
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


def list_daily_observations_between(
    conn,
    *,
    start_date: date,
    end_date: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[DailyObservation]:
    rows = _execute(
        conn,
        """
        SELECT recorded_on, source, weight_kg, body_fat_percent, fat_mass_kg,
               lean_mass_kg, steps, resting_hr, sleep_hours, sleep_score,
               stress_score, hrv_value, hrv_status, body_battery_avg,
               active_calories, total_calories, source_ref
        FROM daily_observations
        WHERE user_id = ? AND recorded_on BETWEEN ? AND ?
        ORDER BY recorded_on ASC,
                 CASE source WHEN 'manual' THEN 0 ELSE 1 END
        """,
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return [
        DailyObservation(
            recorded_on=date.fromisoformat(row["recorded_on"]),
            source=EvidenceSource(row["source"]),
            weight_kg=row["weight_kg"],
            body_fat_percent=row["body_fat_percent"],
            fat_mass_kg=row["fat_mass_kg"],
            lean_mass_kg=row["lean_mass_kg"],
            steps=row["steps"],
            resting_hr=row["resting_hr"],
            sleep_hours=row["sleep_hours"],
            sleep_score=row["sleep_score"],
            stress_score=row["stress_score"],
            hrv_value=row["hrv_value"],
            hrv_status=row["hrv_status"],
            body_battery_avg=row["body_battery_avg"],
            active_calories=row["active_calories"],
            total_calories=row["total_calories"],
            source_ref=row["source_ref"],
        )
        for row in rows
    ]


def upsert_activity(
    conn,
    activity: ActivityRecord,
    *,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO activities (
              user_id, source, external_id, recorded_on, activity_type, name,
              duration_seconds, distance_meters, elevation_gain_meters,
              average_hr, calories, source_ref, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, external_id) DO UPDATE SET
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
                user_id,
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
    conn,
    limit: int = 20,
    *,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[ActivityRecord]:
    rows = _execute(
        conn,
        """
        SELECT source, external_id, recorded_on, activity_type, name,
               duration_seconds, distance_meters, elevation_gain_meters,
               average_hr, calories, source_ref
        FROM activities
        WHERE user_id = ?
        ORDER BY recorded_on DESC, updated_at DESC
        LIMIT ?
        """,
        (user_id, limit),
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


def list_activities_between(
    conn,
    *,
    start_date: date,
    end_date: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[ActivityRecord]:
    rows = _execute(
        conn,
        """
        SELECT source, external_id, recorded_on, activity_type, name,
               duration_seconds, distance_meters, elevation_gain_meters,
               average_hr, calories, source_ref
        FROM activities
        WHERE user_id = ? AND recorded_on BETWEEN ? AND ?
        ORDER BY recorded_on DESC, updated_at DESC
        """,
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return [_activity_record_from_row(row) for row in rows]


def start_connector_sync(
    conn,
    *,
    connector: str,
    start_date: date,
    end_date: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> str:
    sync_id = str(uuid4())
    now = datetime.now(UTC)
    stale_before = now - CONNECTOR_SYNC_STALE_AFTER
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE connector_syncs
            SET status = ?, finished_at = ?, error_message = ?
            WHERE user_id = ? AND connector = ? AND status = ?
              AND started_at < ?
            """,
            (
                ConnectorSyncStatus.FAILED.value,
                now.isoformat(),
                "Previous Garmin sync did not finish.",
                user_id,
                connector,
                ConnectorSyncStatus.RUNNING.value,
                stale_before.isoformat(),
            ),
        )
        cursor = _execute(
            conn,
            """
            INSERT INTO connector_syncs (
              user_id, id, connector, status, started_at, start_date, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                user_id,
                sync_id,
                connector,
                ConnectorSyncStatus.RUNNING.value,
                now.isoformat(),
                start_date.isoformat(),
                end_date.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            raise ConnectorSyncAlreadyRunning(
                f"A {connector} sync is already running."
            )
    return sync_id


def finish_connector_sync(
    conn,
    sync_id: str,
    *,
    status: ConnectorSyncStatus,
    daily_records: int,
    activity_records: int,
    warnings: int,
    error_message: str | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    if status is ConnectorSyncStatus.RUNNING:
        raise ValueError("A finished connector sync cannot remain running.")
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE connector_syncs
            SET status = ?, finished_at = ?, daily_records = ?,
                activity_records = ?, warnings = ?, error_message = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                status.value,
                _utc_now(),
                daily_records,
                activity_records,
                warnings,
                error_message,
                user_id,
                sync_id,
            ),
        )


def fetch_latest_connector_sync(
    conn,
    connector: str,
    *,
    user_id: str = DEFAULT_OWNER_ID,
) -> ConnectorSync | None:
    row = _execute(
        conn,
        """
        SELECT id, connector, status, started_at, finished_at, start_date,
               end_date, daily_records, activity_records, warnings,
               error_message
        FROM connector_syncs
        WHERE user_id = ? AND connector = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (user_id, connector),
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


def list_connector_syncs(
    conn,
    connector: str,
    *,
    limit: int = 100,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[ConnectorSync]:
    rows = _execute(
        conn,
        """
        SELECT id, connector, status, started_at, finished_at, start_date,
               end_date, daily_records, activity_records, warnings,
               error_message
        FROM connector_syncs
        WHERE user_id = ? AND connector = ?
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (user_id, connector, limit),
    ).fetchall()
    return [_connector_sync_from_row(row) for row in rows]


def save_connector_credentials(
    conn,
    *,
    connector: str,
    encrypted_credentials: str,
    status: ConnectorConnectionStatus = ConnectorConnectionStatus.TOKEN_SAVED,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    if not connector.strip():
        raise ValueError("A connector name is required.")
    if not encrypted_credentials:
        raise ValueError("Encrypted connector credentials cannot be empty.")
    now = _utc_now()
    connected_at = now if status is ConnectorConnectionStatus.CONNECTED else None
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO connector_connections (
              user_id, connector, encrypted_credentials, status,
              connected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, connector) DO UPDATE SET
              encrypted_credentials = excluded.encrypted_credentials,
              status = excluded.status,
              connected_at = excluded.connected_at,
              updated_at = excluded.updated_at
            """,
            (
                user_id,
                connector,
                encrypted_credentials,
                status.value,
                connected_at,
                now,
            ),
        )


def update_connector_credentials(
    conn,
    *,
    connector: str,
    encrypted_credentials: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    if not encrypted_credentials:
        raise ValueError("Encrypted connector credentials cannot be empty.")
    with _transaction(conn):
        cursor = _execute(
            conn,
            """
            UPDATE connector_connections
            SET encrypted_credentials = ?, updated_at = ?
            WHERE user_id = ? AND connector = ?
            """,
            (encrypted_credentials, _utc_now(), user_id, connector),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"No saved {connector} connection exists.")


def mark_connector_connected(
    conn,
    *,
    connector: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    now = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE connector_connections
            SET status = ?, connected_at = ?, updated_at = ?
            WHERE user_id = ? AND connector = ?
            """,
            (
                ConnectorConnectionStatus.CONNECTED.value,
                now,
                now,
                user_id,
                connector,
            ),
        )


def mark_connector_reconnect_required(
    conn,
    *,
    connector: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE connector_connections
            SET status = ?, updated_at = ?
            WHERE user_id = ? AND connector = ?
            """,
            (
                ConnectorConnectionStatus.RECONNECT_REQUIRED.value,
                _utc_now(),
                user_id,
                connector,
            ),
        )


def load_connector_credentials(
    conn,
    *,
    connector: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> str | None:
    row = _execute(
        conn,
        """
        SELECT encrypted_credentials
        FROM connector_connections
        WHERE user_id = ? AND connector = ?
        """,
        (user_id, connector),
    ).fetchone()
    return row["encrypted_credentials"] if row else None


def delete_connector_connection(
    conn,
    *,
    connector: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    with _transaction(conn):
        _execute(
            conn,
            """
            DELETE FROM connector_connections
            WHERE user_id = ? AND connector = ?
            """,
            (user_id, connector),
        )


def fetch_connector_connection(
    conn,
    *,
    connector: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> ConnectorConnection | None:
    row = _execute(
        conn,
        """
        SELECT connector, status, connected_at, updated_at
        FROM connector_connections
        WHERE user_id = ? AND connector = ?
        """,
        (user_id, connector),
    ).fetchone()
    if row is None:
        return None
    return ConnectorConnection(
        connector=row["connector"],
        status=ConnectorConnectionStatus(row["status"]),
        connected_at=(
            datetime.fromisoformat(row["connected_at"])
            if row["connected_at"]
            else None
        ),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def database_is_ready(target: DatabaseTarget) -> bool:
    try:
        with connect(target) as conn:
            _execute(conn, "SELECT 1").fetchone()
    except Exception:
        return False
    return True


def _goal_from_row(row) -> Goal:
    return Goal(
        id=row["id"],
        title=row["title"],
        category=GoalCategory(row["category"]),
        priority=GoalPriority(row["priority"]),
        sort_order=row["sort_order"],
        target_value=row["target_value"],
        target_unit=row["target_unit"],
        target_date=(
            date.fromisoformat(row["target_date"]) if row["target_date"] else None
        ),
        supports_goal_id=row["supports_goal_id"],
        archived_at=(
            datetime.fromisoformat(row["archived_at"])
            if row["archived_at"]
            else None
        ),
    )


def _validate_goal_fields(
    *,
    title: str,
    target_value: float | None,
    target_unit: str | None,
) -> tuple[str, str | None]:
    clean_title = title.strip()
    clean_unit = target_unit.strip() if target_unit and target_unit.strip() else None
    if not clean_title:
        raise ValueError("Give the goal a name.")
    if len(clean_title) > 120:
        raise ValueError("Keep the goal name to 120 characters or fewer.")
    if target_value is not None and (
        not isfinite(target_value) or target_value <= 0
    ):
        raise ValueError("A target value must be greater than zero.")
    if (target_value is None) != (clean_unit is None):
        raise ValueError("Add both a target value and its unit, or leave both blank.")
    if clean_unit and len(clean_unit) > 24:
        raise ValueError("Keep the target unit to 24 characters or fewer.")
    return clean_title, clean_unit


def _demote_current_goals(
    conn,
    *,
    user_id: str,
    except_goal_id: str | None,
    now: str,
) -> None:
    parameters: tuple[Any, ...]
    excluding = ""
    if except_goal_id is None:
        parameters = (
            GoalPriority.SUPPORTING.value,
            now,
            user_id,
            GoalPriority.CURRENT.value,
        )
    else:
        excluding = "AND id != ?"
        parameters = (
            GoalPriority.SUPPORTING.value,
            now,
            user_id,
            GoalPriority.CURRENT.value,
            except_goal_id,
        )
    rows = _execute(
        conn,
        f"""
        SELECT id
        FROM goals
        WHERE user_id = ? AND priority = ? AND archived_at IS NULL {excluding}
        """,
        parameters[2:],
    ).fetchall()
    if not rows:
        return
    _execute(
        conn,
        f"""
        UPDATE goals
        SET priority = ?, updated_at = ?
        WHERE user_id = ? AND priority = ? AND archived_at IS NULL {excluding}
        """,
        parameters,
    )
    for row in rows:
        _record_goal_revision(
            conn,
            user_id=user_id,
            goal_id=row["id"],
            effective_from=now,
        )


def _record_goal_revision(
    conn,
    *,
    user_id: str,
    goal_id: str,
    effective_from: str,
) -> None:
    row = _execute(
        conn,
        """
        SELECT id, title, category, priority, sort_order, target_value,
               target_unit, target_date, supports_goal_id, archived_at
        FROM goals
        WHERE user_id = ? AND id = ?
        """,
        (user_id, goal_id),
    ).fetchone()
    if row is None:
        raise LookupError("That goal does not exist.")
    _execute(
        conn,
        """
        INSERT INTO goal_revisions (
          user_id, id, goal_id, title, category, priority, sort_order,
          target_value, target_unit, target_date, supports_goal_id, archived_at,
          effective_from, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(uuid4()),
            goal_id,
            row["title"],
            row["category"],
            row["priority"],
            row["sort_order"],
            row["target_value"],
            row["target_unit"],
            row["target_date"],
            row["supports_goal_id"],
            row["archived_at"],
            effective_from,
            _utc_now(),
        ),
    )


def _ensure_initial_goal_revisions(conn, *, user_id: str) -> None:
    rows = _execute(
        conn,
        """
        SELECT goals.id, goals.created_at
        FROM goals
        WHERE goals.user_id = ?
          AND NOT EXISTS (
            SELECT 1 FROM goal_revisions
            WHERE goal_revisions.user_id = goals.user_id
              AND goal_revisions.goal_id = goals.id
          )
        """,
        (user_id,),
    ).fetchall()
    for row in rows:
        _record_goal_revision(
            conn,
            user_id=user_id,
            goal_id=row["id"],
            effective_from=row["created_at"],
        )


def _evidence_record_from_row(row) -> EvidenceRecord:
    return EvidenceRecord(
        id=row["id"],
        recorded_on=date.fromisoformat(row["recorded_on"]),
        source=EvidenceSource(row["source"]),
        kind=EvidenceKind(row["kind"]),
        value=row["value"],
        unit=row["unit"],
        note=row["note"],
    )


def _activity_record_from_row(row) -> ActivityRecord:
    return ActivityRecord(
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


def _connector_sync_from_row(row) -> ConnectorSync:
    return ConnectorSync(
        id=row["id"],
        connector=row["connector"],
        status=ConnectorSyncStatus(row["status"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=(
            datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
        ),
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        daily_records=row["daily_records"],
        activity_records=row["activity_records"],
        warnings=row["warnings"],
        error_message=row["error_message"],
    )


def _execute(conn, statement: str, parameters: Sequence[Any] = ()):
    if isinstance(conn, sqlite3.Connection):
        return conn.execute(statement, tuple(parameters))
    return conn.execute(statement.replace("?", "%s"), tuple(parameters))


@contextmanager
def _transaction(conn) -> Iterator[None]:
    if isinstance(conn, sqlite3.Connection):
        with conn:
            yield
        return
    with conn.transaction():
        yield


def _migrate_legacy_sqlite_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "goals",
        "evidence_records",
        "daily_observations",
        "activities",
        "connector_syncs",
        "connector_connections",
        "goal_revisions",
    ):
        columns = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "user_id" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'owner'"
            )


def _migrate_goal_columns(conn) -> None:
    if isinstance(conn, sqlite3.Connection):
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(goals)").fetchall()
        }
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE goals ADD COLUMN updated_at TEXT")
        if "archived_at" not in columns:
            conn.execute("ALTER TABLE goals ADD COLUMN archived_at TEXT")
    else:
        _execute(conn, "ALTER TABLE goals ADD COLUMN IF NOT EXISTS updated_at TEXT")
        _execute(conn, "ALTER TABLE goals ADD COLUMN IF NOT EXISTS archived_at TEXT")
    _execute(
        conn,
        "UPDATE goals SET updated_at = created_at WHERE updated_at IS NULL",
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
