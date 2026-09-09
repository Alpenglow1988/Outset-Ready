from datetime import UTC, date, datetime

import pytest

from outset_ready.domain import ConnectorSync, ConnectorSyncStatus
from outset_ready.history import calculate_history_progress


def make_sync(
    start_date: date,
    end_date: date,
    *,
    status: ConnectorSyncStatus = ConnectorSyncStatus.COMPLETED,
) -> ConnectorSync:
    return ConnectorSync(
        id=f"{start_date}:{end_date}",
        connector="garmin",
        status=status,
        started_at=datetime(2026, 9, 9, tzinfo=UTC),
        finished_at=datetime(2026, 9, 9, tzinfo=UTC),
        start_date=start_date,
        end_date=end_date,
        daily_records=(end_date - start_date).days + 1,
        activity_records=0,
        warnings=0,
    )


def test_history_progress_resumes_with_previous_seven_day_batch():
    progress = calculate_history_progress(
        [make_sync(date(2026, 9, 3), date(2026, 9, 9))],
        today=date(2026, 9, 9),
    )

    assert progress.covered_days == 7
    assert progress.remaining_days == 35
    assert progress.next_batch is not None
    assert progress.next_batch.start_date == date(2026, 8, 27)
    assert progress.next_batch.end_date == date(2026, 9, 2)
    assert progress.next_batch.days == 7


def test_history_progress_ignores_failed_intervals_and_fills_gaps():
    progress = calculate_history_progress(
        [
            make_sync(date(2026, 9, 3), date(2026, 9, 9)),
            make_sync(
                date(2026, 8, 27),
                date(2026, 9, 2),
                status=ConnectorSyncStatus.FAILED,
            ),
            make_sync(date(2026, 8, 20), date(2026, 8, 26)),
        ],
        today=date(2026, 9, 9),
    )

    assert progress.covered_days == 14
    assert progress.next_batch is not None
    assert progress.next_batch.start_date == date(2026, 8, 27)
    assert progress.next_batch.end_date == date(2026, 9, 2)


def test_history_progress_counts_warning_syncs_and_stops_when_complete():
    syncs = [
        make_sync(date(2026, 7, 30), date(2026, 8, 5)),
        make_sync(date(2026, 8, 6), date(2026, 8, 12)),
        make_sync(date(2026, 8, 13), date(2026, 8, 19)),
        make_sync(date(2026, 8, 20), date(2026, 8, 26)),
        make_sync(date(2026, 8, 27), date(2026, 9, 2)),
        make_sync(
            date(2026, 9, 3),
            date(2026, 9, 9),
            status=ConnectorSyncStatus.COMPLETED_WITH_WARNINGS,
        ),
    ]

    progress = calculate_history_progress(syncs, today=date(2026, 9, 9))

    assert progress.complete is True
    assert progress.covered_days == 42
    assert progress.remaining_days == 0
    assert progress.next_batch is None


@pytest.mark.parametrize("target_days,batch_days", [(0, 7), (42, 0)])
def test_history_progress_rejects_invalid_windows(target_days, batch_days):
    with pytest.raises(ValueError):
        calculate_history_progress(
            [],
            today=date(2026, 9, 9),
            target_days=target_days,
            batch_days=batch_days,
        )
