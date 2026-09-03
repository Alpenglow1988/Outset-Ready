from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from outset_ready.domain import (
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
        "SELECT COUNT(DISTINCT recorded_on) AS count FROM evidence_records"
    ).fetchone()
    return int(row["count"])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

