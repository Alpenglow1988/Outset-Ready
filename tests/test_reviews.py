import sys
from datetime import UTC, date, datetime, time
from types import SimpleNamespace

import pytest

from outset_ready.domain import (
    EvidenceKind,
    InterpretationStatus,
    WeeklyReviewStatus,
)
from outset_ready.reviews import (
    InterpretationOutput,
    OpenAIWeeklyReviewInterpreter,
    build_review_snapshot,
    load_review_snapshot,
)
from outset_ready.storage import (
    add_manual_evidence,
    claim_weekly_review_interpretation,
    complete_weekly_review_interpretation,
    connect,
    ensure_owner,
    fetch_owner_data_bounds,
    fetch_weekly_review,
    fetch_weekly_review_interpretation,
    finalise_weekly_review,
    init_db,
    list_goals_as_of,
    list_weekly_reviews,
    save_weekly_review_draft,
)
from outset_ready.weekly import build_weekly_read


PERIOD_START = date(2026, 8, 3)
PERIOD_END = date(2026, 8, 9)


def _snapshot(conn):
    goals = list_goals_as_of(
        conn,
        effective_at=datetime.combine(PERIOD_END, time.max, tzinfo=UTC),
    )
    weekly = build_weekly_read(
        conn,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        target_weight_kg=85,
    )
    return build_review_snapshot(weekly, goals)


def test_review_snapshot_is_stable_and_round_trips(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        first = _snapshot(conn)
        second = _snapshot(conn)

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    loaded = load_review_snapshot(first.json)
    assert loaded["weekly_read"]["period_start"] == "2026-08-03"
    assert loaded["weekly_read"]["assessment"]["state"] == "Building a picture"
    assert loaded["weekly_read"]["plan"]["planned_sessions"] == 0
    assert loaded["goals"] == []


def test_changed_evidence_creates_a_new_review_revision(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        first_snapshot = _snapshot(conn)
        first = save_weekly_review_draft(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            evidence_fingerprint=first_snapshot.fingerprint,
            snapshot_json=first_snapshot.json,
        )
        same = save_weekly_review_draft(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            evidence_fingerprint=first_snapshot.fingerprint,
            snapshot_json=first_snapshot.json,
        )
        add_manual_evidence(
            conn,
            recorded_on=PERIOD_END,
            kind=EvidenceKind.WEIGHT_KG,
            value=91.2,
        )
        changed_snapshot = _snapshot(conn)
        changed = save_weekly_review_draft(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            evidence_fingerprint=changed_snapshot.fingerprint,
            snapshot_json=changed_snapshot.json,
        )

        reviews = list_weekly_reviews(conn)

    assert same.id == first.id
    assert changed.id != first.id
    assert changed.revision == 2
    assert [review.revision for review in reviews] == [2, 1]


def test_finalisation_and_interpretation_are_cached_per_revision(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        snapshot = _snapshot(conn)
        review = save_weekly_review_draft(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            evidence_fingerprint=snapshot.fingerprint,
            snapshot_json=snapshot.json,
        )
        with pytest.raises(ValueError, match="before interpreting"):
            claim_weekly_review_interpretation(
                conn,
                review_id=review.id,
                provider="test",
                model="test-model",
                prompt_version="v1",
            )

        finalised = finalise_weekly_review(
            conn,
            review_id=review.id,
            expected_fingerprint=snapshot.fingerprint,
        )
        assert finalised.status is WeeklyReviewStatus.FINALISED
        assert claim_weekly_review_interpretation(
            conn,
            review_id=review.id,
            provider="test",
            model="test-model",
            prompt_version="v1",
        )
        complete_weekly_review_interpretation(
            conn,
            review_id=review.id,
            what_went_well="The plan matched the completed work.",
            main_risk="Sleep evidence needs another week.",
            one_adjustment="Keep the current training shape.",
            encouragement="Use next week to confirm the direction.",
            provider_response_id="response-1",
        )
        assert not claim_weekly_review_interpretation(
            conn,
            review_id=review.id,
            provider="test",
            model="test-model",
            prompt_version="v1",
        )
        interpretation = fetch_weekly_review_interpretation(
            conn,
            review_id=review.id,
        )

    assert interpretation is not None
    assert interpretation.status is InterpretationStatus.COMPLETED
    assert interpretation.provider_response_id == "response-1"


def test_reviews_and_interpretations_are_scoped_to_the_owner(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        ensure_owner(conn, user_id="other", email="other@example.com")
        snapshot = _snapshot(conn)
        review = save_weekly_review_draft(
            conn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            evidence_fingerprint=snapshot.fingerprint,
            snapshot_json=snapshot.json,
        )
        finalise_weekly_review(
            conn,
            review_id=review.id,
            expected_fingerprint=snapshot.fingerprint,
        )
        assert claim_weekly_review_interpretation(
            conn,
            review_id=review.id,
            provider="test",
            model="test-model",
            prompt_version="v1",
        )

        assert fetch_weekly_review(
            conn,
            review_id=review.id,
            user_id="other",
        ) is None
        assert list_weekly_reviews(conn, user_id="other") == []
        assert fetch_weekly_review_interpretation(
            conn,
            review_id=review.id,
            user_id="other",
        ) is None


def test_owner_data_bounds_use_all_weekly_evidence_sources(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    init_db(db_path)

    with connect(db_path) as conn:
        assert fetch_owner_data_bounds(conn) is None
        add_manual_evidence(
            conn,
            recorded_on=PERIOD_END,
            kind=EvidenceKind.WAIST_CM,
            value=112,
        )
        assert fetch_owner_data_bounds(conn) == (PERIOD_END, PERIOD_END)


def test_openai_interpreter_uses_structured_non_stored_response(monkeypatch):
    captured = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="response-1",
                output_parsed=InterpretationOutput(
                    what_went_well="Evidence matched the stated goal.",
                    main_risk="The plan has one unresolved gap.",
                    one_adjustment="Resolve that gap before adding load.",
                    encouragement="Check the same evidence next week.",
                ),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    interpreter = OpenAIWeeklyReviewInterpreter(
        api_key="test-key",
        model="test-model",
    )

    result = interpreter.interpret({"snapshot_version": 1, "weekly_read": {}})

    assert captured["client"]["api_key"] == "test-key"
    assert captured["store"] is False
    assert captured["model"] == "test-model"
    assert captured["text_format"] is InterpretationOutput
    assert "Do not invent facts" in captured["input"][0]["content"]
    assert result.provider_response_id == "response-1"
