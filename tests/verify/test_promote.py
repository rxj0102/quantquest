import sqlite3
from pathlib import Path

import pytest

from core import db
from core.loader import load_questions
from core.schema import Question, Status
from tests.conftest import make_question
from tests.pool import N_TRUSTED
from verify.promote import PromotionReport, main, promote_curated
from verify.references import CURATED_REFERENCES, ExactSpec, Reference


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    db.upsert_questions(c, load_questions())
    yield c
    c.close()


@pytest.fixture(scope="module")
def promoted_report_and_db(tmp_path_factory):
    c = db.connect(":memory:")
    db.upsert_questions(c, load_questions())
    report = promote_curated(c, CURATED_REFERENCES)
    return report, c


def test_promotes_verified_curated_questions(promoted_report_and_db) -> None:
    report, c = promoted_report_and_db
    assert len(report.promoted) == N_TRUSTED
    assert report.flagged == []
    assert [u.question_id for u in report.unverified] == ["stat-005"]
    trusted = db.list_questions(c, status="trusted")
    assert len(trusted) == N_TRUSTED
    assert all(t.verification.result == "pass" for t in trusted)
    assert all(t.status is Status.TRUSTED and t.source == "curated" for t in trusted)


def test_unverifiable_stays_fresh(promoted_report_and_db) -> None:
    _, c = promoted_report_and_db
    q = db.get_question(c, "stat-005")
    assert q.status is Status.FRESH and q.verification.result == "unverified"


def test_promotion_is_idempotent(promoted_report_and_db) -> None:
    _, c = promoted_report_and_db
    again = promote_curated(c, CURATED_REFERENCES)
    assert again.promoted == [] and again.flagged == []
    assert len(db.list_questions(c, status="trusted")) == N_TRUSTED


def test_reload_after_promotion_keeps_trust(promoted_report_and_db) -> None:
    _, c = promoted_report_and_db
    db.upsert_questions(c, load_questions())
    assert len(db.list_questions(c, status="trusted")) == N_TRUSTED


def _break_answer(conn: sqlite3.Connection, qid: str, wrong: str) -> None:
    q = db.get_question(conn, qid)
    db.upsert_questions(conn, [q.model_copy(update={"answer": wrong})])


def test_failed_verification_flags_and_reports(conn: sqlite3.Connection) -> None:
    _break_answer(conn, "prob-001", "1/8")
    report = promote_curated(conn, CURATED_REFERENCES)
    assert [f.question_id for f in report.flagged] == ["prob-001"]
    assert len(report.promoted) == N_TRUSTED - 1
    flagged = db.get_question(conn, "prob-001", include_hidden=True)
    assert flagged.status is Status.FLAGGED and flagged.verification.result == "fail"
    text = report.format()
    assert (
        "prob-001" in text
        and "FLAGGED" in text
        and "1/8" in text
        and flagged.verification.method in text
    )


def test_flagged_questions_are_hidden_from_every_user_facing_query(
    conn: sqlite3.Connection,
) -> None:
    _break_answer(conn, "prob-001", "1/8")
    promote_curated(conn, CURATED_REFERENCES)
    assert db.get_question(conn, "prob-001") is None
    assert "prob-001" not in {x.id for x in db.list_questions(conn)}
    assert "prob-001" not in {x.id for x in db.list_questions(conn, source_kind="curated")}
    assert "prob-001" not in {x.id for x in db.list_questions(conn, status="trusted")}
    assert "prob-001" not in {x.id for x in db.list_questions(conn, status="fresh")}
    with pytest.raises(ValueError):
        db.list_questions(conn, status="flagged")
    assert [x.id for x in db.list_questions(conn, status="flagged", include_hidden=True)] == [
        "prob-001"
    ]
    with pytest.raises(KeyError):
        db.record_attempt(conn, "prob-001", correct=True)


def test_flagged_stays_flagged_on_unchanged_reload_and_resets_when_edited(
    conn: sqlite3.Connection,
) -> None:
    _break_answer(conn, "prob-001", "1/8")
    promote_curated(conn, CURATED_REFERENCES)
    db.upsert_questions(conn, [db.get_question(conn, "prob-001", include_hidden=True)])
    assert db.get_question(conn, "prob-001", include_hidden=True).status is Status.FLAGGED
    db.upsert_questions(conn, [x for x in load_questions() if x.id == "prob-001"])  # fixed in YAML
    assert db.get_question(conn, "prob-001").status is Status.FRESH
    report = promote_curated(conn, CURATED_REFERENCES)
    assert [p.question_id for p in report.promoted] == ["prob-001"]


def test_question_without_reference_stays_fresh(conn: sqlite3.Connection) -> None:
    refs = {k: v for k, v in CURATED_REFERENCES.items() if k != "linalg-001"}
    report = promote_curated(conn, refs)
    assert db.get_question(conn, "linalg-001").status is Status.FRESH
    assert {u.question_id for u in report.unverified} == {"stat-005", "linalg-001"}


def test_error_during_verification_stays_fresh_not_flagged(conn: sqlite3.Connection) -> None:
    refs = {**CURATED_REFERENCES, "linalg-001": Reference(exact=ExactSpec(expr="__import__('os')"))}
    report = promote_curated(conn, refs)
    assert db.get_question(conn, "linalg-001").status is Status.FRESH
    assert report.flagged == []


def test_only_curated_fresh_questions_are_considered(conn: sqlite3.Connection) -> None:
    gen = Question.model_validate(make_question(id="gen-001", source="generated", answer="5/14"))
    db.upsert_questions(conn, [gen])
    refs = {**CURATED_REFERENCES, "gen-001": CURATED_REFERENCES["prob-002"]}
    promote_curated(conn, refs)
    assert db.get_question(conn, "gen-001").status is Status.FRESH


def test_report_format_when_clean() -> None:
    text = PromotionReport().format()
    assert "promoted 0" in text.lower() and "FLAGGED" not in text


def test_cli_exit_codes_and_failure_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    from core.loader import DEFAULT_DIR

    path = tmp_path / "qq.db"
    assert main(["--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"promoted {N_TRUSTED}" in out.lower() and "stat-005" in out

    # Someone edits the YAML to a wrong answer; the next run must flag it, not trust it.
    data = tmp_path / "curated"
    shutil.copytree(DEFAULT_DIR, data)
    f = data / "statistics.yaml"
    f.write_text(f.read_text().replace('answer: "17"', 'answer: "18"'))
    assert main(["--db", str(path), "--data", str(data)]) == 1
    out = capsys.readouterr().out
    assert "FLAGGED" in out and "stat-002" in out and "18" in out

    c = db.connect(path)
    assert db.get_question(c, "stat-002") is None
    c.close()
