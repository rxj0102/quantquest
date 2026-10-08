"""Sizes of the real curated pool, derived from the data so tests do not hard-code them."""

from core.loader import load_questions
from core.schema import AnswerType

_QUESTIONS = load_questions()
N_CURATED = len(_QUESTIONS)
# every non-text answer is verified and promoted; text answers can never be trusted
N_TRUSTED = sum(q.answer_type is not AnswerType.TEXT for q in _QUESTIONS)
