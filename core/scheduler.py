"""Spaced-repetition scheduling behind a small interface (SM-2 now, FSRS later).

Interface
---------
A ``Scheduler`` has two pure methods and no hidden state or clock:

* ``initial(user_id, question_id, now)``: the state of a card that has never been reviewed
* ``review(state, rating, now)``: the next state after answering at time ``now``

``core.review`` persists the states. To swap in FSRS, implement the same two methods; nothing
else changes.

Rating -> SM-2 quality
----------------------
The app collects one of four ratings. SM-2 grades an answer with a quality ``q`` from 0 to 5,
where ``q < 3`` is a failure (a lapse) and ``q >= 3`` is a pass. The mapping is::

    AGAIN -> q = 1   failed to recall: a lapse
    HARD  -> q = 3   recalled with serious difficulty: the weakest pass
    GOOD  -> q = 4   recalled after some hesitation: the neutral pass
    EASY  -> q = 5   perfect, instant recall

(q = 0 and q = 2 are unused: four ratings cannot cover six grades, and FSRS also uses four.)

Algorithm (SM-2, day granularity, no intra-day learning steps)
---------------------------------------------------------------
On a pass (HARD, GOOD, EASY):

* the interval is 1 day after the first success, 6 days after the second, and afterwards
  ``floor(previous_interval * ease + 0.5)`` using the ease *before* this review is applied
* ease changes by ``0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)``: +0.10 for EASY, 0 for GOOD and
  -0.14 for HARD, never below the floor of 1.3
* the interval is capped at ``max_interval`` days (3650 by default)

On AGAIN (a lapse):

* the repetition count restarts at 0 and the interval becomes 1 day, so the card climbs the
  1 day, 6 days, ``interval * ease`` ladder again
* ``lapses`` increases by 1 and ease drops by a fixed 0.2 (floor 1.3). Deviation from the
  paper: canonical SM-2 leaves the ease unchanged on a failure, and applying the quality
  formula with q = 1 would cost 0.54, which is too harsh. A flat 0.2 follows common practice.

A card is due at ``now + interval`` days, where ``now`` is the time of the review. All
datetimes are timezone-aware UTC; naive datetimes raise ``ValueError``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.timeutil import to_utc


class Rating(StrEnum):
    """How well the user answered."""

    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


# See "Rating -> SM-2 quality" in the module docstring.
QUALITY: dict[Rating, int] = {Rating.AGAIN: 1, Rating.HARD: 3, Rating.GOOD: 4, Rating.EASY: 5}

MIN_EASE = 1.3


class ReviewState(BaseModel):
    """Spaced-repetition state of one question for one user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    ease: float = Field(default=2.5, ge=MIN_EASE, allow_inf_nan=False)
    interval_days: int = Field(default=0, ge=0)
    repetitions: int = Field(default=0, ge=0)
    lapses: int = Field(default=0, ge=0)
    due_at: datetime
    last_reviewed_at: datetime | None = None

    @field_validator("due_at", "last_reviewed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)


@runtime_checkable
class Scheduler(Protocol):
    """What the rest of the app needs from a scheduling algorithm."""

    def initial(self, user_id: str, question_id: str, now: datetime) -> ReviewState:
        """State of a never-reviewed card."""
        ...

    def review(self, state: ReviewState, rating: Rating, now: datetime) -> ReviewState:
        """Next state after the user answers at ``now`` with ``rating``."""
        ...


class SM2:
    """The SM-2 algorithm; see the module docstring for the exact rules."""

    def __init__(
        self,
        initial_ease: float = 2.5,
        lapse_penalty: float = 0.2,
        max_interval: int = 3650,
    ) -> None:
        self.initial_ease = initial_ease
        self.lapse_penalty = lapse_penalty
        self.max_interval = max_interval

    def initial(self, user_id: str, question_id: str, now: datetime) -> ReviewState:
        """A card that has not been reviewed yet; it is due immediately."""
        return ReviewState(
            user_id=user_id, question_id=question_id, ease=self.initial_ease, due_at=to_utc(now)
        )

    def review(self, state: ReviewState, rating: Rating, now: datetime) -> ReviewState:
        """Apply one answer and return the new state (the input is not modified)."""
        now = to_utc(now)
        quality = QUALITY[Rating(rating)]
        if quality < 3:
            interval = 1
            repetitions = 0
            lapses = state.lapses + 1
            ease = max(MIN_EASE, round(state.ease - self.lapse_penalty, 4))
        else:
            if state.repetitions == 0:
                interval = 1
            elif state.repetitions == 1:
                interval = 6
            else:
                interval = math.floor(state.interval_days * state.ease + 0.5)
            interval = min(interval, self.max_interval)
            repetitions = state.repetitions + 1
            lapses = state.lapses
            gap = 5 - quality
            ease = max(MIN_EASE, round(state.ease + 0.1 - gap * (0.08 + gap * 0.02), 4))
        return state.model_copy(
            update={
                "ease": ease,
                "interval_days": interval,
                "repetitions": repetitions,
                "lapses": lapses,
                "due_at": now + timedelta(days=interval),
                "last_reviewed_at": now,
            }
        )
