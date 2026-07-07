from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from noteflow_worker.config import settings
from noteflow_worker.study.models import REVIEW_GRADES, ReviewState


QUALITY = {"AGAIN": 1, "HARD": 3, "GOOD": 4, "EASY": 5}


def schedule_review(state: ReviewState, grade: str, now: datetime | None = None) -> ReviewState:
    """Deterministic SM-2 scheduling with four user-facing grades."""
    grade = grade.upper()
    if grade not in REVIEW_GRADES:
        raise ValueError(f"Unsupported review grade: {grade}")
    if state.status == "SUSPENDED":
        raise ValueError("A suspended card cannot be reviewed.")
    now = now or datetime.now(timezone.utc)
    quality = QUALITY[grade]
    ease = max(
        settings.srs_min_ease,
        state.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)),
    )
    if grade == "AGAIN":
        repetitions, interval, status = 0, 1, "LEARNING"
    else:
        repetitions = state.repetitions + 1
        if repetitions == 1:
            interval = settings.srs_first_interval_days
        elif repetitions == 2:
            interval = settings.srs_second_interval_days
        else:
            interval = max(1, round(state.interval_days * ease))
        if grade == "HARD":
            interval = max(1, round(interval * 0.8))
        elif grade == "EASY":
            interval = max(1, round(interval * 1.3))
        status = "REVIEW" if repetitions >= 2 else "LEARNING"
    return replace(
        state,
        status=status,
        ease_factor=round(ease, 4),
        interval_days=interval,
        repetitions=repetitions,
        due_at=now + timedelta(days=interval),
        last_reviewed_at=now,
        last_grade=grade,
    )


def reset_review(state: ReviewState, now: datetime | None = None) -> ReviewState:
    now = now or datetime.now(timezone.utc)
    return replace(state, status="NEW", ease_factor=settings.srs_initial_ease, interval_days=0,
                   repetitions=0, due_at=now, last_reviewed_at=None, last_grade=None)


def suspend_review(state: ReviewState) -> ReviewState:
    return replace(state, status="SUSPENDED")


def resume_review(state: ReviewState, now: datetime | None = None) -> ReviewState:
    if state.status != "SUSPENDED":
        return state
    now = now or datetime.now(timezone.utc)
    status = "REVIEW" if state.repetitions >= 2 else "LEARNING" if state.repetitions else "NEW"
    return replace(state, status=status, due_at=state.due_at or now)
