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


@pytest.fixture
def trusted_conn(curated: list[Question]):
    """In-memory DB with the curated pool loaded and every non-text question trusted.

    Verification is recorded directly (fast); real verification is covered in tests/verify.
    """
    from core import db
    from core.schema import AnswerType, Status, Verification

    conn = db.connect(":memory:")
    db.upsert_questions(conn, curated)
    ok = Verification(method="test_fixture", result="pass", details="fixture")
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            db.set_verification(conn, q.id, ok, Status.TRUSTED)
    yield conn
    conn.close()
