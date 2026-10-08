import pytest

from core.loader import load_questions
from core.similarity import (
    FLAG_JACCARD,
    FLAG_RATIO,
    compare,
    nearest,
    normalize,
    pairwise_max,
)

PROMPT = (
    "A drawer holds $5$ red socks and $3$ blue socks. You pull out two socks at random without "
    "replacement. What is the probability that both are red?"
)


def test_normalize_masks_numbers_and_strips_latex_and_punctuation() -> None:
    assert normalize("Roll $2$ dice: $\\frac{1}{2}$, or 3.5?") == "roll # dice # # or #"
    assert normalize("  A   B\n\tC ") == "a b c"
    assert normalize("") == ""


def test_identical_and_renumbered_copies_are_flagged() -> None:
    assert compare(PROMPT, PROMPT).flagged and compare(PROMPT, PROMPT).score == 1.0
    renumbered = PROMPT.replace("$5$", "$9$").replace("$3$", "$2$")
    assert compare(PROMPT, renumbered).flagged


def test_unrelated_prompts_are_not_flagged() -> None:
    other = "Let $X$ be exponential with rate $\\lambda$. Give $\\mathbb{E}[X^2]$ in terms of $\\lambda$."
    assert not compare(PROMPT, other).flagged


def test_a_reworded_version_scores_lower_than_a_renumbered_one() -> None:
    reworded = (
        "From a box of $5$ red and $3$ blue pens take two without putting any back; both red?"
    )
    renumbered = PROMPT.replace("$5$", "$9$")
    assert compare(PROMPT, reworded).score < compare(PROMPT, renumbered).score


def test_thresholds_are_documented_values() -> None:
    assert (FLAG_JACCARD, FLAG_RATIO) == (0.35, 0.80)


def test_nearest_is_sorted_limited_and_deterministic() -> None:
    pool = {
        "a": PROMPT,
        "b": PROMPT.replace("socks", "pens"),
        "c": "Compute the determinant of a matrix.",
    }
    got = nearest(PROMPT, pool, k=2)
    assert [qid for qid, _ in got] == ["a", "b"]
    assert got[0][1].score >= got[1][1].score
    assert nearest(PROMPT, pool, k=5) == nearest(PROMPT, pool, k=5)
    assert len(nearest(PROMPT, pool, k=1)) == 1 and nearest(PROMPT, {}, k=3) == []


def test_pairwise_max_never_pairs_a_prompt_with_itself() -> None:
    pool = {"a": PROMPT, "b": "Completely different words about eigenvalues of matrices."}
    pm = pairwise_max(pool)
    assert pm["a"][0] == "b" and pm["b"][0] == "a"


def test_the_curated_pool_has_no_near_duplicates_by_these_thresholds() -> None:
    """Keeps the thresholds honest: if this fails, either the pool or the thresholds drifted."""
    pool = {q.id: q.prompt_md for q in load_questions()}
    flagged = {a: (b, s) for a, (b, s) in pairwise_max(pool).items() if s.flagged}
    assert flagged == {}


@pytest.mark.parametrize("qid", [q.id for q in load_questions()])
def test_every_curated_prompt_flags_a_renumbered_copy_of_itself(qid: str) -> None:
    prompt = {q.id: q.prompt_md for q in load_questions()}[qid]
    assert compare(prompt, prompt.replace("$2$", "$7$").replace("2", "8")).flagged
