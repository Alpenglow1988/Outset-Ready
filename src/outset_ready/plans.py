from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from uuid import uuid4

from outset_ready.domain import (
    ActivityRecord,
    ActivityType,
    EvidenceSource,
    PlanChangeReason,
    PlanChangeType,
    PlanImportItem,
    PlanMatchMethod,
    PlanSource,
    PlannedSession,
    PlannedSessionRevision,
    PlannedSessionStatus,
)
from outset_ready.storage import (
    DEFAULT_OWNER_ID,
    _execute,
    _transaction,
    _utc_now,
    list_activities_between,
)


OWNER_CHANGE_TYPES = frozenset(
    {
        PlanChangeType.EDITED,
        PlanChangeType.MOVED,
        PlanChangeType.REPLACED,
        PlanChangeType.SHORTENED,
    }
)


@dataclass(frozen=True)
class PlanSessionRow:
    session: PlannedSession
    matched_activity: ActivityRecord | None
    match_method: PlanMatchMethod | None


@dataclass(frozen=True)
class PlanWeek:
    period_start: date
    period_end: date
    sessions: tuple[PlanSessionRow, ...]
    unmatched_activities: tuple[ActivityRecord, ...]
    revisions: tuple[PlannedSessionRevision, ...]

    @property
    def planned_sessions(self) -> int:
        return sum(
            row.session.status is PlannedSessionStatus.PLANNED
            for row in self.sessions
        )

    @property
    def completed_sessions(self) -> int:
        return sum(
            row.matched_activity is not None
            and row.session.status is PlannedSessionStatus.PLANNED
            for row in self.sessions
        )

    @property
    def skipped_sessions(self) -> int:
        return sum(
            row.session.status is PlannedSessionStatus.SKIPPED
            for row in self.sessions
        )

    def due_unmatched_sessions(self, *, through_date: date) -> int:
        return sum(
            row.session.status is PlannedSessionStatus.PLANNED
            and row.session.scheduled_on <= through_date
            and row.matched_activity is None
            for row in self.sessions
        )


def import_plan_snapshot(
    conn,
    *,
    source: PlanSource,
    start_date: date,
    end_date: date,
    items: list[PlanImportItem],
    user_id: str = DEFAULT_OWNER_ID,
) -> str:
    if source is PlanSource.MANUAL:
        raise ValueError("Manual sessions do not use provider snapshots.")
    if end_date < start_date:
        raise ValueError("The plan snapshot end cannot precede its start.")
    if any(not start_date <= item.scheduled_on <= end_date for item in items):
        raise ValueError("Every imported session must fall inside the snapshot window.")
    identities = [item.external_id for item in items]
    if any(not identity.strip() for identity in identities):
        raise ValueError("Every imported session needs a provider identity.")
    if len(set(identities)) != len(identities):
        raise ValueError("A provider snapshot cannot contain duplicate session IDs.")

    snapshot_id = str(uuid4())
    captured_at = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO plan_snapshots (
              user_id, id, source, start_date, end_date, captured_at, item_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                snapshot_id,
                source.value,
                start_date.isoformat(),
                end_date.isoformat(),
                captured_at,
                len(items),
            ),
        )
        for item in items:
            clean_title = _validate_session_fields(
                title=item.title,
                duration_seconds=item.planned_duration_seconds,
                distance_meters=item.planned_distance_meters,
            )
            _execute(
                conn,
                """
                INSERT INTO plan_snapshot_sessions (
                  user_id, snapshot_id, external_id, scheduled_on,
                  activity_type, title, planned_duration_seconds,
                  planned_distance_meters, source_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    snapshot_id,
                    item.external_id,
                    item.scheduled_on.isoformat(),
                    item.activity_type.value,
                    clean_title,
                    item.planned_duration_seconds,
                    item.planned_distance_meters,
                    item.source_ref,
                ),
            )
            _upsert_provider_session(
                conn,
                source=source,
                item=item,
                clean_title=clean_title,
                changed_at=captured_at,
                user_id=user_id,
            )

        imported_ids = set(identities)
        existing_rows = _execute(
            conn,
            """
            SELECT id, external_id
            FROM planned_sessions
            WHERE user_id = ? AND source = ?
              AND scheduled_on BETWEEN ? AND ?
              AND status != ? AND manual_override = 0
            """,
            (
                user_id,
                source.value,
                start_date.isoformat(),
                end_date.isoformat(),
                PlannedSessionStatus.REMOVED.value,
            ),
        ).fetchall()
        for row in existing_rows:
            if row["external_id"] in imported_ids:
                continue
            _execute(
                conn,
                """
                UPDATE planned_sessions
                SET status = ?, updated_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    PlannedSessionStatus.REMOVED.value,
                    captured_at,
                    user_id,
                    row["id"],
                ),
            )
            _record_revision(
                conn,
                planned_session_id=row["id"],
                change_type=PlanChangeType.PROVIDER_REMOVED,
                actor="garmin",
                changed_at=captured_at,
                user_id=user_id,
            )
    return snapshot_id


def create_manual_session(
    conn,
    *,
    scheduled_on: date,
    activity_type: ActivityType,
    title: str,
    planned_duration_seconds: float | None = None,
    planned_distance_meters: float | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlannedSession:
    clean_title = _validate_session_fields(
        title=title,
        duration_seconds=planned_duration_seconds,
        distance_meters=planned_distance_meters,
    )
    session_id = str(uuid4())
    now = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            INSERT INTO planned_sessions (
              user_id, id, source, external_id, scheduled_on, activity_type,
              title, planned_duration_seconds, planned_distance_meters, status,
              manual_override, provider_fingerprint, source_ref, created_at,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                PlanSource.MANUAL.value,
                None,
                scheduled_on.isoformat(),
                activity_type.value,
                clean_title,
                planned_duration_seconds,
                planned_distance_meters,
                PlannedSessionStatus.PLANNED.value,
                1,
                None,
                None,
                now,
                now,
            ),
        )
        _record_revision(
            conn,
            planned_session_id=session_id,
            change_type=PlanChangeType.ADDED,
            actor="owner",
            changed_at=now,
            user_id=user_id,
        )
    return fetch_planned_session(conn, session_id=session_id, user_id=user_id)


def update_planned_session(
    conn,
    *,
    session_id: str,
    scheduled_on: date,
    activity_type: ActivityType,
    title: str,
    planned_duration_seconds: float | None,
    planned_distance_meters: float | None,
    change_type: PlanChangeType,
    reason: PlanChangeReason | None = None,
    reason_note: str | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlannedSession:
    if change_type not in OWNER_CHANGE_TYPES:
        raise ValueError("Choose moved, replaced, shortened or edited.")
    clean_title = _validate_session_fields(
        title=title,
        duration_seconds=planned_duration_seconds,
        distance_meters=planned_distance_meters,
    )
    clean_note = _validate_reason_note(reason_note)
    current = fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    if current.status is PlannedSessionStatus.REMOVED:
        raise ValueError("A provider-removed session cannot be edited.")
    _validate_change_type(
        current,
        scheduled_on=scheduled_on,
        activity_type=activity_type,
        title=clean_title,
        duration_seconds=planned_duration_seconds,
        distance_meters=planned_distance_meters,
        change_type=change_type,
    )
    now = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE planned_sessions
            SET scheduled_on = ?, activity_type = ?, title = ?,
                planned_duration_seconds = ?, planned_distance_meters = ?,
                manual_override = 1, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                scheduled_on.isoformat(),
                activity_type.value,
                clean_title,
                planned_duration_seconds,
                planned_distance_meters,
                now,
                user_id,
                session_id,
            ),
        )
        _record_revision(
            conn,
            planned_session_id=session_id,
            change_type=change_type,
            actor="owner",
            changed_at=now,
            reason=reason,
            reason_note=clean_note,
            user_id=user_id,
        )
    return fetch_planned_session(conn, session_id=session_id, user_id=user_id)


def skip_planned_session(
    conn,
    *,
    session_id: str,
    reason: PlanChangeReason | None = None,
    reason_note: str | None = None,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlannedSession:
    clean_note = _validate_reason_note(reason_note)
    session = fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    if session.status is not PlannedSessionStatus.PLANNED:
        raise ValueError("Only a planned session can be skipped.")
    existing_match = _execute(
        conn,
        """
        SELECT 1 FROM planned_activity_matches
        WHERE user_id = ? AND planned_session_id = ?
        """,
        (user_id, session_id),
    ).fetchone()
    if existing_match is not None:
        raise ValueError("Remove the completed activity match before skipping this session.")
    now = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE planned_sessions
            SET status = ?, manual_override = 1, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (PlannedSessionStatus.SKIPPED.value, now, user_id, session_id),
        )
        _record_revision(
            conn,
            planned_session_id=session_id,
            change_type=PlanChangeType.SKIPPED,
            actor="owner",
            changed_at=now,
            reason=reason,
            reason_note=clean_note,
            user_id=user_id,
        )
    return fetch_planned_session(conn, session_id=session_id, user_id=user_id)


def restore_planned_session(
    conn,
    *,
    session_id: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlannedSession:
    session = fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    if session.status is not PlannedSessionStatus.SKIPPED:
        raise ValueError("Only a skipped session can be restored.")
    now = _utc_now()
    with _transaction(conn):
        _execute(
            conn,
            """
            UPDATE planned_sessions
            SET status = ?, manual_override = 1, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (PlannedSessionStatus.PLANNED.value, now, user_id, session_id),
        )
        _record_revision(
            conn,
            planned_session_id=session_id,
            change_type=PlanChangeType.RESTORED,
            actor="owner",
            changed_at=now,
            user_id=user_id,
        )
    return fetch_planned_session(conn, session_id=session_id, user_id=user_id)


def fetch_planned_session(
    conn,
    *,
    session_id: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlannedSession:
    row = _execute(
        conn,
        """
        SELECT id, source, external_id, scheduled_on, activity_type, title,
               planned_duration_seconds, planned_distance_meters, status,
               manual_override
        FROM planned_sessions
        WHERE user_id = ? AND id = ?
        """,
        (user_id, session_id),
    ).fetchone()
    if row is None:
        raise LookupError("That planned session does not exist.")
    return _session_from_row(row)


def list_planned_sessions(
    conn,
    *,
    start_date: date,
    end_date: date,
    include_removed: bool = False,
    user_id: str = DEFAULT_OWNER_ID,
) -> list[PlannedSession]:
    removed_filter = "" if include_removed else "AND status != 'removed'"
    rows = _execute(
        conn,
        f"""
        SELECT id, source, external_id, scheduled_on, activity_type, title,
               planned_duration_seconds, planned_distance_meters, status,
               manual_override
        FROM planned_sessions
        WHERE user_id = ? AND scheduled_on BETWEEN ? AND ? {removed_filter}
        ORDER BY scheduled_on, created_at, id
        """,
        (user_id, start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return [_session_from_row(row) for row in rows]


def list_plan_revisions(
    conn,
    *,
    session_ids: list[str],
    user_id: str = DEFAULT_OWNER_ID,
) -> list[PlannedSessionRevision]:
    if not session_ids:
        return []
    placeholders = ", ".join("?" for _ in session_ids)
    rows = _execute(
        conn,
        f"""
        SELECT id, planned_session_id, change_type, actor, changed_at,
               reason, reason_note
        FROM planned_session_revisions
        WHERE user_id = ? AND planned_session_id IN ({placeholders})
        ORDER BY changed_at DESC, id DESC
        """,
        (user_id, *session_ids),
    ).fetchall()
    return [
        PlannedSessionRevision(
            id=row["id"],
            planned_session_id=row["planned_session_id"],
            change_type=PlanChangeType(row["change_type"]),
            actor=row["actor"],
            changed_at=datetime.fromisoformat(row["changed_at"]),
            reason=PlanChangeReason(row["reason"]) if row["reason"] else None,
            reason_note=row["reason_note"],
        )
        for row in rows
    ]


def match_planned_session(
    conn,
    *,
    session_id: str,
    activity_source: EvidenceSource,
    activity_external_id: str,
    method: PlanMatchMethod = PlanMatchMethod.MANUAL,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    session = fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    if session.status is not PlannedSessionStatus.PLANNED:
        raise ValueError("Only an active planned session can match an activity.")
    activity = _execute(
        conn,
        """
        SELECT recorded_on FROM activities
        WHERE user_id = ? AND source = ? AND external_id = ?
        """,
        (user_id, activity_source.value, activity_external_id),
    ).fetchone()
    if activity is None:
        raise LookupError("That activity does not exist.")
    activity_date = date.fromisoformat(activity["recorded_on"])
    week_start = session.scheduled_on - timedelta(days=session.scheduled_on.weekday())
    if not week_start <= activity_date <= week_start + timedelta(days=6):
        raise ValueError("A planned session can only match activity from the same week.")
    with _transaction(conn):
        existing_match = _execute(
            conn,
            """
            SELECT planned_session_id
            FROM planned_activity_matches
            WHERE user_id = ? AND activity_source = ? AND activity_external_id = ?
            """,
            (user_id, activity_source.value, activity_external_id),
        ).fetchone()
        if existing_match is not None and existing_match["planned_session_id"] != session_id:
            raise ValueError("That activity already matches another session.")
        _execute(
            conn,
            """
            DELETE FROM planned_activity_matches
            WHERE user_id = ? AND planned_session_id = ?
            """,
            (user_id, session_id),
        )
        _execute(
            conn,
            """
            INSERT INTO planned_activity_matches (
              user_id, planned_session_id, activity_source,
              activity_external_id, match_method, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                activity_source.value,
                activity_external_id,
                method.value,
                _utc_now(),
            ),
        )


def unmatch_planned_session(
    conn,
    *,
    session_id: str,
    user_id: str = DEFAULT_OWNER_ID,
) -> None:
    fetch_planned_session(conn, session_id=session_id, user_id=user_id)
    with _transaction(conn):
        _execute(
            conn,
            """
            DELETE FROM planned_activity_matches
            WHERE user_id = ? AND planned_session_id = ?
            """,
            (user_id, session_id),
        )


def auto_match_planned_sessions(
    conn,
    *,
    start_date: date,
    end_date: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> int:
    sessions = list_planned_sessions(
        conn,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )
    activities = list_activities_between(
        conn,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )
    matches = _match_rows(conn, user_id=user_id)
    used = {(row["activity_source"], row["activity_external_id"]) for row in matches}
    matched_sessions = {row["planned_session_id"] for row in matches}
    eligible_sessions = [
        session
        for session in sessions
        if session.status is PlannedSessionStatus.PLANNED
        and session.id not in matched_sessions
    ]
    available_activities = [
        activity
        for activity in activities
        if (activity.source.value, activity.external_id) not in used
    ]
    sessions_by_key: dict[tuple[date, ActivityType], list[PlannedSession]] = {}
    activities_by_key: dict[tuple[date, ActivityType], list[ActivityRecord]] = {}
    for session in eligible_sessions:
        sessions_by_key.setdefault(
            (session.scheduled_on, session.activity_type), []
        ).append(session)
    for activity in available_activities:
        activities_by_key.setdefault(
            (activity.recorded_on, activity.activity_type), []
        ).append(activity)

    created = 0
    for key, matching_sessions in sessions_by_key.items():
        matching_activities = activities_by_key.get(key, [])
        if len(matching_sessions) != 1 or len(matching_activities) != 1:
            continue
        session = matching_sessions[0]
        activity = matching_activities[0]
        match_planned_session(
            conn,
            session_id=session.id,
            activity_source=activity.source,
            activity_external_id=activity.external_id,
            method=PlanMatchMethod.AUTOMATIC,
            user_id=user_id,
        )
        created += 1
    return created


def build_plan_week(
    conn,
    *,
    period_start: date,
    period_end: date,
    user_id: str = DEFAULT_OWNER_ID,
) -> PlanWeek:
    sessions = list_planned_sessions(
        conn,
        start_date=period_start,
        end_date=period_end,
        include_removed=True,
        user_id=user_id,
    )
    activities = list_activities_between(
        conn,
        start_date=period_start,
        end_date=period_end,
        user_id=user_id,
    )
    activities_by_identity = {
        (activity.source.value, activity.external_id): activity
        for activity in activities
    }
    match_rows = _match_rows(conn, user_id=user_id)
    matches_by_session = {row["planned_session_id"]: row for row in match_rows}
    used_activities: set[tuple[str, str]] = set()
    session_rows: list[PlanSessionRow] = []
    for session in sessions:
        match = matches_by_session.get(session.id)
        identity = (
            (match["activity_source"], match["activity_external_id"])
            if match
            else None
        )
        activity = activities_by_identity.get(identity) if identity else None
        if identity and activity:
            used_activities.add(identity)
        session_rows.append(
            PlanSessionRow(
                session=session,
                matched_activity=activity,
                match_method=PlanMatchMethod(match["match_method"]) if match else None,
            )
        )
    revisions = list_plan_revisions(
        conn,
        session_ids=[session.id for session in sessions],
        user_id=user_id,
    )
    return PlanWeek(
        period_start=period_start,
        period_end=period_end,
        sessions=tuple(session_rows),
        unmatched_activities=tuple(
            activity
            for activity in activities
            if (activity.source.value, activity.external_id) not in used_activities
        ),
        revisions=tuple(revisions),
    )


def _upsert_provider_session(
    conn,
    *,
    source: PlanSource,
    item: PlanImportItem,
    clean_title: str,
    changed_at: str,
    user_id: str,
) -> None:
    fingerprint = _provider_fingerprint(item, clean_title=clean_title)
    existing = _execute(
        conn,
        """
        SELECT id, manual_override, provider_fingerprint, status
        FROM planned_sessions
        WHERE user_id = ? AND source = ? AND external_id = ?
        """,
        (user_id, source.value, item.external_id),
    ).fetchone()
    if existing is None:
        session_id = str(uuid4())
        _execute(
            conn,
            """
            INSERT INTO planned_sessions (
              user_id, id, source, external_id, scheduled_on, activity_type,
              title, planned_duration_seconds, planned_distance_meters, status,
              manual_override, provider_fingerprint, source_ref, created_at,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                source.value,
                item.external_id,
                item.scheduled_on.isoformat(),
                item.activity_type.value,
                clean_title,
                item.planned_duration_seconds,
                item.planned_distance_meters,
                PlannedSessionStatus.PLANNED.value,
                0,
                fingerprint,
                item.source_ref,
                changed_at,
                changed_at,
            ),
        )
        _record_revision(
            conn,
            planned_session_id=session_id,
            change_type=PlanChangeType.IMPORTED,
            actor="garmin",
            changed_at=changed_at,
            user_id=user_id,
        )
        return

    if existing["manual_override"]:
        _execute(
            conn,
            """
            UPDATE planned_sessions
            SET provider_fingerprint = ?, source_ref = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (fingerprint, item.source_ref, changed_at, user_id, existing["id"]),
        )
        return

    changed = (
        existing["provider_fingerprint"] != fingerprint
        or existing["status"] == PlannedSessionStatus.REMOVED.value
    )
    if not changed:
        return
    _execute(
        conn,
        """
        UPDATE planned_sessions
        SET scheduled_on = ?, activity_type = ?, title = ?,
            planned_duration_seconds = ?, planned_distance_meters = ?,
            status = ?, provider_fingerprint = ?, source_ref = ?, updated_at = ?
        WHERE user_id = ? AND id = ?
        """,
        (
            item.scheduled_on.isoformat(),
            item.activity_type.value,
            clean_title,
            item.planned_duration_seconds,
            item.planned_distance_meters,
            PlannedSessionStatus.PLANNED.value,
            fingerprint,
            item.source_ref,
            changed_at,
            user_id,
            existing["id"],
        ),
    )
    _record_revision(
        conn,
        planned_session_id=existing["id"],
        change_type=PlanChangeType.PROVIDER_UPDATED,
        actor="garmin",
        changed_at=changed_at,
        user_id=user_id,
    )


def _record_revision(
    conn,
    *,
    planned_session_id: str,
    change_type: PlanChangeType,
    actor: str,
    changed_at: str,
    reason: PlanChangeReason | None = None,
    reason_note: str | None = None,
    user_id: str,
) -> None:
    row = _execute(
        conn,
        """
        SELECT scheduled_on, activity_type, title, planned_duration_seconds,
               planned_distance_meters, status
        FROM planned_sessions
        WHERE user_id = ? AND id = ?
        """,
        (user_id, planned_session_id),
    ).fetchone()
    if row is None:
        raise LookupError("That planned session does not exist.")
    _execute(
        conn,
        """
        INSERT INTO planned_session_revisions (
          user_id, id, planned_session_id, change_type, actor, scheduled_on,
          activity_type, title, planned_duration_seconds,
          planned_distance_meters, status, reason, reason_note, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(uuid4()),
            planned_session_id,
            change_type.value,
            actor,
            row["scheduled_on"],
            row["activity_type"],
            row["title"],
            row["planned_duration_seconds"],
            row["planned_distance_meters"],
            row["status"],
            reason.value if reason else None,
            reason_note,
            changed_at,
        ),
    )


def _session_from_row(row) -> PlannedSession:
    return PlannedSession(
        id=row["id"],
        source=PlanSource(row["source"]),
        external_id=row["external_id"],
        scheduled_on=date.fromisoformat(row["scheduled_on"]),
        activity_type=ActivityType(row["activity_type"]),
        title=row["title"],
        planned_duration_seconds=row["planned_duration_seconds"],
        planned_distance_meters=row["planned_distance_meters"],
        status=PlannedSessionStatus(row["status"]),
        manual_override=bool(row["manual_override"]),
    )


def _match_rows(conn, *, user_id: str):
    return _execute(
        conn,
        """
        SELECT planned_session_id, activity_source, activity_external_id,
               match_method
        FROM planned_activity_matches
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()


def _validate_session_fields(
    *,
    title: str,
    duration_seconds: float | None,
    distance_meters: float | None,
) -> str:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Give the planned session a name.")
    if len(clean_title) > 120:
        raise ValueError("Keep the session name to 120 characters or fewer.")
    for value, label in (
        (duration_seconds, "duration"),
        (distance_meters, "distance"),
    ):
        if value is not None and (not isfinite(value) or value <= 0):
            raise ValueError(f"The planned {label} must be greater than zero.")
    return clean_title


def _validate_reason_note(reason_note: str | None) -> str | None:
    clean_note = reason_note.strip() if reason_note and reason_note.strip() else None
    if clean_note and len(clean_note) > 240:
        raise ValueError("Keep the change note to 240 characters or fewer.")
    return clean_note


def _validate_change_type(
    current: PlannedSession,
    *,
    scheduled_on: date,
    activity_type: ActivityType,
    title: str,
    duration_seconds: float | None,
    distance_meters: float | None,
    change_type: PlanChangeType,
) -> None:
    if change_type is PlanChangeType.MOVED and scheduled_on == current.scheduled_on:
        raise ValueError("A moved session needs a different date.")
    if (
        change_type is PlanChangeType.REPLACED
        and activity_type is current.activity_type
        and title == current.title
    ):
        raise ValueError("A replacement needs a different activity type or name.")
    if change_type is PlanChangeType.SHORTENED:
        duration_reduced = (
            current.planned_duration_seconds is not None
            and duration_seconds is not None
            and duration_seconds < current.planned_duration_seconds
        )
        distance_reduced = (
            current.planned_distance_meters is not None
            and distance_meters is not None
            and distance_meters < current.planned_distance_meters
        )
        if not (duration_reduced or distance_reduced):
            raise ValueError("A shortened session needs a smaller duration or distance.")


def _provider_fingerprint(item: PlanImportItem, *, clean_title: str) -> str:
    payload = json.dumps(
        {
            "scheduled_on": item.scheduled_on.isoformat(),
            "activity_type": item.activity_type.value,
            "title": clean_title,
            "duration": item.planned_duration_seconds,
            "distance": item.planned_distance_meters,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
