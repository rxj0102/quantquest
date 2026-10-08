import sqlite3
from pathlib import Path

import pytest

from core import db
from core.loader import load_questions
from core.schema import Question, Status
from tests.conftest import make_question


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    yield c
    c.close()


def test_table_and_indexes_created(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    assert {
        "id",
        "topic",
        "node_id",
        "difficulty",
        "format",
        "prompt_md",
        "answer",
        "answer_type",
        "solution_md",
        "verification",
        "source",
        "status",
        "created_at",
        "solve_stats",
    } <= cols
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(questions)")}
    assert "idx_questions_pool" in idx


def test_all_curated_round_trip(conn: sqlite3.Connection, curated: list[Question]) -> None:
    db.upsert_questions(conn, curated)
    assert len(db.list_questions(conn)) == 20
    for q in curated:
        assert db.get_question(conn, q.id) == q


def test_round_trip_preserves_latex_and_unicode(conn: sqlite3.Connection) -> None:
    q = Question.model_validate(
        make_question(prompt_md="Compute $\\frac{1}{2}\\sum_i x_i$ — naïve σ?")
    )
    db.upsert_questions(conn, [q])
    assert db.get_question(conn, q.id) == q


def test_round_trip_through_file_database(tmp_path: Path, curated: list[Question]) -> None:
    path = tmp_path / "qq.db"
    c1 = db.connect(path)
    db.upsert_questions(c1, curated)
    c1.close()
    c2 = db.connect(path)
    assert db.list_questions(c2) == sorted(curated, key=lambda q: q.id)
    c2.close()


def test_get_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert db.get_question(conn, "prob-404") is None


def test_pools_stay_separate(conn: sqlite3.Connection, curated: list[Question]) -> None:
    gen = Question.model_validate(make_question(id="gen-001", source="generated"))
    db.upsert_questions(conn, [*curated, gen])
    assert [q.id for q in db.list_questions(conn, source_kind="generated")] == ["gen-001"]
    assert len(db.list_questions(conn, source_kind="curated")) == 20
    assert db.list_questions(conn, source_kind="paper") == []


def test_list_filter_by_status(conn: sqlite3.Connection, curated: list[Question]) -> None:
    db.upsert_questions(conn, curated)
    assert len(db.list_questions(conn, status="fresh")) == 20
    assert db.list_questions(conn, status="trusted") == []


def test_db_rejects_bad_enum_values(conn: sqlite3.Connection) -> None:
    db.upsert_questions(conn, [Question.model_validate(make_question())])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE questions SET status = 'bogus'")


def test_reload_is_idempotent(conn: sqlite3.Connection, curated: list[Question]) -> None:
    db.upsert_questions(conn, curated)
    db.upsert_questions(conn, load_questions())
    assert db.list_questions(conn) == sorted(curated, key=lambda q: q.id)


def test_reload_does_not_overwrite_solve_stats(
    conn: sqlite3.Connection, curated: list[Question]
) -> None:
    db.upsert_questions(conn, curated)
    db.record_attempt(conn, "prob-001", correct=True)
    db.record_attempt(conn, "prob-001", correct=False)
    db.record_attempt(conn, "prob-002", correct=True)
    before = {q.id: q.solve_stats for q in db.list_questions(conn)}
    assert before["prob-001"].attempts == 2 and before["prob-001"].correct == 1

    db.upsert_questions(conn, load_questions())  # re-running the loader

    after = {q.id: q.solve_stats for q in db.list_questions(conn)}
    assert after == before
    assert after["prob-001"].attempts == 2
    assert after["prob-002"].correct == 1


def test_reload_preserves_created_at(conn: sqlite3.Connection, curated: list[Question]) -> None:
    db.upsert_questions(conn, curated)
    before = db.get_question(conn, "prob-001").created_at
    db.upsert_questions(conn, load_questions())  # new Question objects get a newer created_at
    assert db.get_question(conn, "prob-001").created_at == before


def test_reload_updates_content_but_keeps_stats(conn: sqlite3.Connection) -> None:
    db.upsert_questions(conn, [Question.model_validate(make_question())])
    db.record_attempt(conn, "prob-999", correct=True)
    edited = Question.model_validate(make_question(solution_md="Better explanation."))
    db.upsert_questions(conn, [edited])
    got = db.get_question(conn, "prob-999")
    assert got.solution_md == "Better explanation."
    assert got.solve_stats.attempts == 1


def test_reload_keeps_review_state_unless_content_changes(conn: sqlite3.Connection) -> None:
    ver = {"method": "mc", "result": "pass"}
    trusted = make_question(status="trusted", verification=ver)
    db.upsert_questions(conn, [Question.model_validate(trusted)])
    # Re-load of unchanged content (YAML says fresh/unverified): DB keeps status and verification.
    db.upsert_questions(conn, [Question.model_validate(make_question())])
    got = db.get_question(conn, "prob-999")
    assert got.status is Status.TRUSTED and got.verification.result == "pass"
    # Answer edited in YAML: earlier review no longer applies.
    db.upsert_questions(conn, [Question.model_validate(make_question(answer="1/3"))])
    got = db.get_question(conn, "prob-999")
    assert got.answer == "1/3"
    assert got.status is Status.FRESH and got.verification.result == "unverified"


def test_record_attempt_unknown_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        db.record_attempt(conn, "prob-404", correct=True)


# --- M2: hidden statuses and set_verification ---------------------------------------------------


def test_flagged_and_retired_are_hidden_by_default(
    conn: sqlite3.Connection, curated: list[Question]
) -> None:
    db.upsert_questions(conn, curated)
    for qid, status in [("prob-001", "flagged"), ("prob-002", "retired")]:
        conn.execute("UPDATE questions SET status = ? WHERE id = ?", (status, qid))
    ids = {q.id for q in db.list_questions(conn)}
    assert "prob-001" not in ids and "prob-002" not in ids and len(ids) == 18
    assert db.get_question(conn, "prob-001") is None
    assert db.get_question(conn, "prob-002", include_hidden=True) is not None
    all_ids = {q.id for q in db.list_questions(conn, include_hidden=True)}
    assert {"prob-001", "prob-002"} <= all_ids
    with pytest.raises(ValueError):
        db.list_questions(conn, status="retired")
    with pytest.raises(KeyError):
        db.record_attempt(conn, "prob-001", correct=True)


def test_set_verification_promotes_and_validates(conn: sqlite3.Connection) -> None:
    from pydantic import ValidationError

    from core.schema import Verification

    db.upsert_questions(conn, [Question.model_validate(make_question())])
    with pytest.raises(ValidationError):  # trusted needs a passing record (CLAUDE.md rule 3)
        db.set_verification(
            conn, "prob-999", Verification(method="x", result="fail"), Status.TRUSTED
        )
    assert db.get_question(conn, "prob-999").status is Status.FRESH
    ok = Verification(method="sympy_numeric", result="pass", details="rel_tol=1e-9")
    db.set_verification(conn, "prob-999", ok, Status.TRUSTED)
    got = db.get_question(conn, "prob-999")
    assert got.status is Status.TRUSTED and got.verification == ok
    with pytest.raises(KeyError):
        db.set_verification(conn, "prob-404", ok, Status.TRUSTED)


def test_set_verification_keeps_solve_stats(conn: sqlite3.Connection) -> None:
    from core.schema import Verification

    db.upsert_questions(conn, [Question.model_validate(make_question())])
    db.record_attempt(conn, "prob-999", correct=True)
    ok = Verification(method="m", result="pass")
    db.set_verification(conn, "prob-999", ok, Status.TRUSTED)
    assert db.get_question(conn, "prob-999").solve_stats.attempts == 1
