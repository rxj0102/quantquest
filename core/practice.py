"""Practice sessions: what to show, which ratings are allowed, and recording a rating.

The queue is the user's due reviews first (most overdue first), then never-seen questions from
unlocked or mastered nodes. Only trusted questions from the curated pool are ever considered
(``db.list_playable``); code questions wait for the code-challenge milestone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from core import db, progress, review
from core.scheduler import Rating, ReviewState
from core.schema import AnswerType, Question
from core.skill_tree import SkillTree, node_states
from verify.answers import AnswerResult

__all__ = ["Card", "Rating", "RatingOutcome", "allowed_ratings", "build_queue", "rate"]


@dataclass(frozen=True)
class Card:
    """One question in a session, and why it is there."""

    question: Question
    kind: Literal["due", "new"]


def build_queue(
    conn: sqlite3.Connection,
    tree: SkillTree,
    user_id: str,
    now: datetime,
    *,
    max_cards: int = 20,
    max_new: int = 10,
) -> list[Card]:
    """Due cards first, then new cards from unlocked nodes; at most ``max_cards`` in total."""
    playable = {q.id: q for q in db.list_playable(conn) if q.answer_type is not AnswerType.CODE}
    states = node_states(tree, playable.values())
    due = [playable[q.id] for q in review.next_due(conn, user_id, now) if q.id in playable]
    fresh = [
        playable[q.id]
        for q in review.unseen_trusted(conn, user_id)
        if q.id in playable and q.node_id in states and states[q.node_id].status != "locked"
    ]
    fresh.sort(key=lambda q: (tree.order.index(q.node_id), q.difficulty, q.id))
    cards = [Card(q, "due") for q in due[: max(0, max_cards)]]
    room = max(0, max_cards - len(cards))
    cards += [Card(q, "new") for q in fresh[: min(max(0, max_new), room)]]
    return cards


def allowed_ratings(result: AnswerResult) -> list[Rating]:
    """Ratings the user may pick after this answer.

    A wrong answer can only be AGAIN (you cannot rate a miss Good), an unreadable answer must be
    retried, and correct or self-graded answers may pick any rating.
    """
    if result.outcome == "unreadable":
        return []
    if result.outcome == "incorrect":
        return [Rating.AGAIN]
    return [Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.EASY]


@dataclass(frozen=True)
class RatingOutcome:
    """The new review state and the XP just earned."""

    state: ReviewState
    xp: int


def rate(
    conn: sqlite3.Connection,
    user_id: str,
    question_id: str,
    rating: Rating,
    now: datetime,
    result: AnswerResult,
) -> RatingOutcome:
    """Record a rating: schedule the next review, count the attempt and award XP, atomically.

    Raises ValueError if the rating is not allowed for the answer, or the question is not
    trusted and curated.
    """
    rating = Rating(rating)
    if rating not in allowed_ratings(result):
        raise ValueError(f"rating {rating.value!r} is not allowed after a {result.outcome} answer")
    question = db.get_playable(conn, question_id)
    if question is None:
        raise ValueError(f"question {question_id} is not playable (trusted curated only)")
    xp = progress.xp_for_review(question.difficulty, rating)

    def award(c: sqlite3.Connection) -> None:
        progress.insert_xp_event(c, user_id, question_id, "review", xp, now)

    state = review.record_review(conn, user_id, question_id, rating, now, extra_writes=award)
    return RatingOutcome(state, xp)
