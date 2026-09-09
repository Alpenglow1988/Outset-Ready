from __future__ import annotations

from datetime import date, timedelta


def last_completed_training_week(today: date) -> tuple[date, date]:
    """Return the last fully completed Monday-to-Sunday week."""
    period_end = today - timedelta(days=today.weekday() + 1)
    period_start = period_end - timedelta(days=6)
    return period_start, period_end
