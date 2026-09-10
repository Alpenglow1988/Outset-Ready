from datetime import date

from outset_ready.periods import current_training_week, last_completed_training_week


def test_last_completed_training_week_uses_monday_to_sunday():
    assert last_completed_training_week(date(2026, 9, 9)) == (
        date(2026, 8, 31),
        date(2026, 9, 6),
    )


def test_last_completed_training_week_on_monday_returns_previous_week():
    assert last_completed_training_week(date(2026, 9, 7)) == (
        date(2026, 8, 31),
        date(2026, 9, 6),
    )


def test_current_training_week_uses_monday_to_sunday():
    assert current_training_week(date(2026, 9, 9)) == (
        date(2026, 9, 7),
        date(2026, 9, 13),
    )
