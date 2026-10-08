"""The 1e-3 tolerance is for typed answers in the UI only. Promotion stays strict."""

import inspect
from pathlib import Path

import pytest

from core import db
from core.config import Settings
from core.schema import Question, Status
from tests.conftest import make_question
from verify import dispatch, promote, sympy_check
from verify.answers import check_typed_answer
from verify.dispatch import verify_question
from verify.promote import promote_curated
from verify.references import ExactSpec, Reference, SimulatorSpec
from verify.sympy_check import DEFAULT_ABS_TOL, DEFAULT_REL_TOL, check_numeric

DICE = SimulatorSpec(name="dice_sum_equals", params={"n_dice": 2, "sides": 6, "target": 9})
REF = Reference(exact=ExactSpec(expr="1/9"), simulator=DICE)


def test_verifier_defaults_are_the_strict_ones() -> None:
    assert DEFAULT_REL_TOL == 1e-9 and DEFAULT_ABS_TOL == 1e-12
    assert Reference().rel_tol == 1e-9
    assert inspect.signature(check_numeric).parameters["rel_tol"].default == 1e-9


def test_the_same_near_miss_passes_in_the_ui_and_fails_promotion() -> None:
    """0.1111 is 1e-4 (relative) away from 1/9: fine for a typed answer, wrong for a stored one."""
    truth = Question.model_validate(make_question(answer="1/9", format="probability"))
    assert check_typed_answer(truth, "0.1111", ui_rel_tol=1e-3).outcome == "correct"
    stored_wrong = Question.model_validate(make_question(answer="0.1111", format="probability"))
    assert verify_question(stored_wrong, REF).result == "fail"
    assert check_numeric("0.1111", ExactSpec(expr="1/9")).outcome == "fail"


def test_a_huge_ui_tolerance_does_not_loosen_promotion() -> None:
    loose = Settings(ui_numeric_rel_tol=0.5)
    truth = Question.model_validate(make_question(answer="1/9", format="probability"))
    assert (
        check_typed_answer(truth, "0.15", ui_rel_tol=loose.ui_numeric_rel_tol).outcome == "correct"
    )
    wrong = Question.model_validate(make_question(answer="0.15", format="probability"))
    assert verify_question(wrong, REF).result == "fail"  # settings are not even consulted


def test_a_per_question_ui_override_does_not_loosen_promotion() -> None:
    wrong = Question.model_validate(
        make_question(answer="0.1111", format="probability", answer_tolerance=0.5)
    )
    assert verify_question(wrong, REF).result == "fail"


def test_promotion_flags_an_inexact_stored_answer_in_a_real_database() -> None:
    conn = db.connect(":memory:")
    q = Question.model_validate(make_question(id="prob-901", answer="0.1111", format="probability"))
    db.upsert_questions(conn, [q])
    report = promote_curated(conn, {"prob-901": REF})
    assert [f.question_id for f in report.flagged] == ["prob-901"]
    assert db.get_question(conn, "prob-901") is None
    assert db.get_question(conn, "prob-901", include_hidden=True).status is Status.FLAGGED


@pytest.mark.parametrize("module", [dispatch, promote, sympy_check])
def test_verification_code_never_reads_the_ui_settings(module) -> None:
    src = inspect.getsource(module)
    assert "core.config" not in src and "ui_numeric_rel_tol" not in src
    assert "answer_tolerance" not in src


def test_the_ui_tolerance_lives_only_in_the_answer_checker_and_config() -> None:
    root = Path(__file__).resolve().parent.parent
    hits = {
        p.relative_to(root).as_posix()
        for folder in ("verify", "core")
        for p in (root / folder).rglob("*.py")
        if "ui_numeric_rel_tol" in p.read_text() or "ui_rel_tol" in p.read_text()
    }
    allowed = {"core/config.py", "verify/answers.py", "core/interview.py", "core/practice.py"}
    assert hits <= allowed, hits - allowed
