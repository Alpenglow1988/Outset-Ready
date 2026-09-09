from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from outset_ready.domain import ConnectorSync, ConnectorSyncStatus


HISTORY_TARGET_DAYS = 42
HISTORY_BATCH_DAYS = 7
SUCCESSFUL_SYNC_STATUSES = frozenset(
    {
        ConnectorSyncStatus.COMPLETED,
        ConnectorSyncStatus.COMPLETED_WITH_WARNINGS,
    }
)


@dataclass(frozen=True)
class HistoryBatch:
    start_date: date
    end_date: date

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


@dataclass(frozen=True)
class HistoryProgress:
    target_start: date
    target_end: date
    covered_days: int
    target_days: int
    latest_covered_date: date | None
    next_batch: HistoryBatch | None

    @property
    def complete(self) -> bool:
        return self.covered_days >= self.target_days

    @property
    def remaining_days(self) -> int:
        return max(self.target_days - self.covered_days, 0)


def calculate_history_progress(
    syncs: list[ConnectorSync],
    *,
    today: date,
    target_days: int = HISTORY_TARGET_DAYS,
    batch_days: int = HISTORY_BATCH_DAYS,
) -> HistoryProgress:
    if target_days < 1:
        raise ValueError("target_days must be at least 1")
    if batch_days < 1:
        raise ValueError("batch_days must be at least 1")

    target_end = today
    target_start = today - timedelta(days=target_days - 1)
    covered: set[date] = set()
    for sync in syncs:
        if sync.status not in SUCCESSFUL_SYNC_STATUSES:
            continue
        interval_start = max(sync.start_date, target_start)
        interval_end = min(sync.end_date, target_end)
        if interval_start > interval_end:
            continue
        for offset in range((interval_end - interval_start).days + 1):
            covered.add(interval_start + timedelta(days=offset))

    missing = [
        target_start + timedelta(days=offset)
        for offset in range(target_days)
        if target_start + timedelta(days=offset) not in covered
    ]
    next_batch = _next_missing_batch(missing, batch_days=batch_days)
    return HistoryProgress(
        target_start=target_start,
        target_end=target_end,
        covered_days=len(covered),
        target_days=target_days,
        latest_covered_date=max(covered, default=None),
        next_batch=next_batch,
    )


def _next_missing_batch(
    missing: list[date],
    *,
    batch_days: int,
) -> HistoryBatch | None:
    if not missing:
        return None

    missing_dates = set(missing)
    batch_end = max(missing)
    batch_start = batch_end
    while (
        (batch_end - batch_start).days + 1 < batch_days
        and batch_start - timedelta(days=1) in missing_dates
    ):
        batch_start -= timedelta(days=1)
    return HistoryBatch(start_date=batch_start, end_date=batch_end)
