from outset_ready.readiness import (
    ReadinessSignals,
    ReadinessState,
    assess_readiness,
)


def test_builds_a_picture_until_comparable_evidence_exists():
    assessment = assess_readiness(ReadinessSignals(evidence_days=2))
    assert assessment.state is ReadinessState.BUILDING_A_PICTURE


def test_reviews_plan_when_many_planned_sessions_are_missing():
    assessment = assess_readiness(
        ReadinessSignals(
            evidence_days=7,
            goal_trend_supporting=True,
            planned_sessions=6,
            completed_sessions=2,
        )
    )
    assert assessment.state is ReadinessState.REVIEW_THE_PLAN


def test_progresses_when_trend_and_actions_align():
    assessment = assess_readiness(
        ReadinessSignals(
            evidence_days=7,
            goal_trend_supporting=True,
            planned_sessions=5,
            completed_sessions=4,
        )
    )
    assert assessment.state is ReadinessState.PROGRESSING


def test_uses_mixed_signals_for_non_decisive_evidence():
    assessment = assess_readiness(
        ReadinessSignals(
            evidence_days=7,
            goal_trend_supporting=False,
            planned_sessions=5,
            completed_sessions=4,
        )
    )
    assert assessment.state is ReadinessState.MIXED_SIGNALS

