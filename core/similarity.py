"""Near-duplicate detection for question prompts (no external dependencies).

Prompts are normalised before comparison: lowercased, LaTeX commands and punctuation removed, and
every number replaced by ``#``, so "the same problem with different numbers" still looks the same.
Two measures are reported: Jaccard overlap of word 3-grams (shared phrasing) and a
``difflib`` sequence-matcher ratio (shared structure). A pair is flagged if either crosses its
threshold. The thresholds were chosen so that no pair in the curated pool is flagged (a test keeps
it that way) while a re-numbered copy of a question always is.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping
from dataclasses import dataclass

FLAG_JACCARD = 0.35
FLAG_RATIO = 0.80

_LATEX = re.compile(r"\\[a-zA-Z]+")
_NON_ALNUM = re.compile(r"[^a-z0-9#]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalize(text: str) -> str:
    """Lowercase, drop LaTeX commands and punctuation, mask numbers as ``#``."""
    text = _LATEX.sub(" ", text.lower())
    text = _NUMBER.sub("#", text)
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _shingles(tokens: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


@dataclass(frozen=True)
class Similarity:
    """How alike two prompts are."""

    jaccard: float
    ratio: float

    @property
    def score(self) -> float:
        """The larger of the two measures."""
        return max(self.jaccard, self.ratio)

    @property
    def flagged(self) -> bool:
        """True if the pair looks like a near-duplicate."""
        return self.jaccard >= FLAG_JACCARD or self.ratio >= FLAG_RATIO


def compare(a: str, b: str) -> Similarity:
    """Similarity of two prompts (after normalisation)."""
    na, nb = normalize(a), normalize(b)
    sa, sb = _shingles(na.split()), _shingles(nb.split())
    union = sa | sb
    jaccard = len(sa & sb) / len(union) if union else 0.0
    return Similarity(jaccard, difflib.SequenceMatcher(None, na, nb).ratio())


def nearest(text: str, pool: Mapping[str, str], k: int = 3) -> list[tuple[str, Similarity]]:
    """The ``k`` most similar prompts in ``pool`` (id -> prompt), most similar first."""
    scored = [(qid, compare(text, other)) for qid, other in pool.items()]
    scored.sort(key=lambda item: (-item[1].score, item[0]))
    return scored[:k]


def pairwise_max(pool: Mapping[str, str]) -> dict[str, tuple[str, Similarity]]:
    """For each prompt, its most similar *other* prompt (used to calibrate the thresholds)."""
    return {
        qid: nearest(text, {o: t for o, t in pool.items() if o != qid}, 1)[0]
        for qid, text in pool.items()
    }
