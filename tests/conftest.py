import pytest

from core.loader import load_questions
from core.schema import Question


@pytest.fixture(scope="session")
def curated() -> list[Question]:
    return load_questions()


def make_question(**overrides: object) -> dict[str, object]:
    """A valid raw question dict; override fields to build invalid variants."""
    base: dict[str, object] = {
        "id": "prob-999",
        "topic": "probability",
        "node_id": "prob.counting",
        "difficulty": 2,
        "format": "probability",
        "prompt_md": "Flip $2$ fair coins. P(two heads)?",
        "answer": "1/4",
        "answer_type": "numeric",
        "solution_md": "$(1/2)^2$.",
        "source": "curated",
    }
    return {**base, **overrides}
