"""The draft-checking tool: lints, verification, similarity, third checks, and isolation."""

from pathlib import Path

import pytest
import yaml

from core.loader import DEFAULT_DIR, load_questions
from scripts.draft_check import (
    Issue,
    check_drafts,
    format_is_stated,
    format_report,
    main,
)

DRAFTS = Path(__file__).resolve().parent.parent / "data" / "drafts"


def entry(**over: object) -> dict:
    base = {
        "id": "prob-901",
        "topic": "probability",
        "node_id": "prob.conditional",
        "difficulty": 2,
        "format": "mental_math",
        "prompt_md": "A fair coin is tossed three times. Given that at least one toss is heads, "
        "what is the probability that all three are heads? Give your answer as a fraction in "
        "lowest terms.",
        "answer": "1/7",
        "answer_type": "numeric",
        "solution_md": "There are $7$ outcomes with a head and one of them is HHH.",
        "source": "curated",
        "status": "fresh",
        "reference": {"exact": {"expr": "Rational(1, 7)"}},
    }
    return {**base, **over}


def write(tmp_path: Path, *entries: dict, name: str = "d.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(list(entries), sort_keys=False, width=1000))
    return path


def run(tmp_path: Path, *entries: dict, **kw):
    return check_drafts(write(tmp_path, *entries), **kw)


def errors(report) -> list[str]:
    return [i.message for i in report.issues if i.level == "error"]


# --- the happy path -----------------------------------------------------------------------------


def test_a_good_draft_has_no_issues_and_passes_verification(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry())
    assert errors(rep) == [] and rep.verification.result == "pass"
    assert rep.nearest and all(
        qid.count("-") == 1 for qid, _ in rep.nearest
    )  # compared with the pool
    assert len(rep.nearest) == 3


def test_the_report_sheet_shows_prompt_answer_reference_verifier_and_similarity(
    tmp_path: Path,
) -> None:
    reports = run(tmp_path, entry())
    text = format_report(reports)
    assert "prob-901" in text and "Given that at least one toss is heads" in text
    assert "1/7" in text and "Rational(1, 7)" in text
    assert "pass" in text and "sympy_numeric" in text
    assert "nearest" in text.lower() and "jaccard" in text.lower()
    assert "SUMMARY" in text and "1 draft" in text


# --- lints ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Find it. Give your answer as a fraction in lowest terms.",
        "Round your answer to 3 decimal places.",
        "Give a decimal to 4 significant figures.",
        "Give the exact value.",
        "Answer with a single expression in terms of $n$.",
        "Give your answer as an integer.",
        "Report the exact expression.",
        "Reply with a whole number.",
    ],
)
def test_format_stated(prompt: str) -> None:
    assert format_is_stated(prompt)


@pytest.mark.parametrize("prompt", ["What is the probability?", "Compute the determinant.", ""])
def test_format_not_stated(prompt: str) -> None:
    assert not format_is_stated(prompt)


def test_a_prompt_without_an_answer_format_is_an_error(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(prompt_md="Given a head, what is the chance of three heads?"))
    assert any("answer format" in m for m in errors(rep))


def test_a_symbolic_answer_must_be_a_single_expression(tmp_path: Path) -> None:
    sym = entry(
        answer_type="symbolic",
        answer="2/n",
        prompt_md="Find the variance of the estimator as a single expression in $n$.",
        reference={"exact": {"expr": "2/n"}},
    )
    (ok,) = run(tmp_path, sym)
    assert errors(ok) == []
    (many,) = run(tmp_path, {**sym, "answer": "1/n, 2/n"})
    assert any("single expression" in m for m in errors(many))
    (prompt,) = run(
        tmp_path, {**sym, "prompt_md": "Find all values of the variance in terms of $n$."}
    )
    assert any("single expression" in m for m in errors(prompt))
    (nomention,) = run(tmp_path, {**sym, "prompt_md": "Find the variance in terms of $n$."})
    assert any("single expression" in m for m in errors(nomention))


def test_id_collisions_with_the_curated_pool_are_errors(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(id="prob-001"))
    assert any("already used by the curated pool" in m for m in errors(rep))


def test_duplicate_ids_across_drafts_are_errors(tmp_path: Path) -> None:
    a, b = run(tmp_path, entry(), entry())
    assert any("duplicate draft id" in m for m in errors(a) + errors(b))


def test_unknown_node_is_an_error(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(node_id="prob.nonexistent"))
    assert any("skill-tree node" in m for m in errors(rep))


def test_drafts_must_be_fresh_curated_and_in_the_seeding_difficulty_range(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(status="trusted", verification={"method": "x", "result": "pass"}))
    assert any("status must be fresh" in m for m in errors(rep))
    (rep,) = run(tmp_path, entry(difficulty=5))
    assert any("difficulty" in m for m in errors(rep))


def test_numeric_drafts_need_an_exact_reference(tmp_path: Path) -> None:
    e = entry()
    e.pop("reference")
    (rep,) = run(tmp_path, e)
    assert any("reference" in m for m in errors(rep))
    assert rep.verification.result == "unverified"


def test_probability_format_needs_a_simulator(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(format="probability"))
    assert any("simulator" in m for m in errors(rep))


def test_a_wrong_answer_fails_verification(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(answer="1/3"))
    assert rep.verification.result == "fail"
    assert main([str(write(tmp_path, entry(answer="1/3")))]) == 1


# --- similarity --------------------------------------------------------------------------------------


def test_a_renumbered_copy_of_a_curated_question_is_an_error(tmp_path: Path) -> None:
    curated = {q.id: q for q in load_questions()}["prob-002"]
    copy = entry(
        prompt_md=curated.prompt_md.replace("$5$", "$6$").replace("$3$", "$4$")
        + " Give your answer as a fraction in lowest terms."
    )
    (rep,) = run(tmp_path, copy)
    assert any("near-duplicate" in m and "prob-002" in m for m in errors(rep))


def test_drafts_are_compared_with_each_other_too(tmp_path: Path) -> None:
    a = entry()
    b = entry(id="prob-902", prompt_md=a["prompt_md"].replace("three", "four"))
    ra, rb = run(tmp_path, a, b)
    assert any("prob-902" in m for m in errors(ra)) and any("prob-901" in m for m in errors(rb))


def test_same_answer_in_the_same_node_is_a_warning_not_an_error(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(answer="2/3", reference={"exact": {"expr": "2/3"}}))
    assert rep.same_answer == ["prob-006"] or "prob-006" not in rep.same_answer  # shape only
    same = entry(answer="19/217", reference={"exact": {"expr": "19/217"}})
    (rep,) = run(tmp_path, same)
    assert "prob-006" in rep.same_answer
    assert any(i.level == "warn" and "prob-006" in i.message for i in rep.issues)


# --- third check ---------------------------------------------------------------------------------------


def test_the_independent_third_check_must_agree(tmp_path: Path) -> None:
    (ok,) = run(
        tmp_path, entry(), third_checks={"prob-901": lambda: __import__("fractions").Fraction(1, 7)}
    )
    assert ok.third_check == "agrees" and errors(ok) == []
    (bad,) = run(
        tmp_path, entry(), third_checks={"prob-901": lambda: __import__("fractions").Fraction(1, 6)}
    )
    assert bad.third_check == "DISAGREES" and any("third check" in m for m in errors(bad))


def test_a_missing_third_check_is_only_a_warning(tmp_path: Path) -> None:
    (rep,) = run(tmp_path, entry(), third_checks={})
    assert rep.third_check == "none" and errors(rep) == []
    assert any(i.level == "warn" and "third check" in i.message for i in rep.issues)


def test_a_crashing_third_check_is_an_error(tmp_path: Path) -> None:
    def boom() -> None:
        raise RuntimeError("x")

    (rep,) = run(tmp_path, entry(), third_checks={"prob-901": boom})
    assert any("third check" in m for m in errors(rep))


# --- CLI ---------------------------------------------------------------------------------------------------


def test_cli_exit_codes_and_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(write(tmp_path, entry())), "--no-third-check"]) == 0
    out = capsys.readouterr().out
    assert "prob-901" in out and "SUMMARY" in out
    assert main([str(write(tmp_path, entry(prompt_md="Plain question?"), name="e.yaml"))]) == 1


def test_cli_accepts_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, entry(), name="a.yaml")
    write(
        tmp_path,
        entry(
            id="prob-902",
            prompt_md="Totally different: roll a fair die; what is the "
            "chance of an even number given it exceeds two? Give your answer as a fraction in lowest terms.",
            answer="1/2",
            reference={"exact": {"expr": "Rational(1, 2)"}},
        ),
        name="b.yaml",
    )
    assert main([str(tmp_path), "--no-third-check"]) == 0
    assert "2 drafts" in capsys.readouterr().out


def test_issue_is_a_simple_value_object() -> None:
    assert Issue("error", "x").level == "error"


# --- isolation: drafts never reach the app or the database ---------------------------------------------


def test_the_default_data_dir_is_curated_not_drafts() -> None:
    assert (
        DEFAULT_DIR.name == "curated"
        and DRAFTS.name == "drafts"
        and DRAFTS.parent == DEFAULT_DIR.parent
    )


def test_draft_ids_never_collide_with_the_curated_pool_or_each_other() -> None:
    from core.loader import load_file_entries

    curated = {q.id for q in load_questions()}
    seen: dict[str, Path] = {}
    for path in sorted(DRAFTS.glob("*.yaml")):
        for e in load_file_entries(path):
            assert e.question.id not in curated, (
                f"{e.question.id} in {path.name} collides with curated"
            )
            assert e.question.id not in seen, f"{e.question.id} duplicated in {path.name}"
            seen[e.question.id] = path


def test_bootstrap_never_loads_drafts(tmp_path: Path) -> None:
    from app.bootstrap import bootstrap
    from core import db
    from core.loader import load_file_entries

    draft_ids = {e.question.id for p in DRAFTS.glob("*.yaml") for e in load_file_entries(p)}
    bootstrap(str(tmp_path / "qq.db"))
    conn = db.connect(tmp_path / "qq.db")
    stored = {r[0] for r in conn.execute("SELECT id FROM questions")}
    conn.close()
    assert not stored & draft_ids and len(stored) == 20


@pytest.mark.parametrize("path", sorted(DRAFTS.glob("*.yaml")), ids=lambda p: p.name)
def test_every_real_draft_file_lints_clean(path: Path) -> None:
    reports = check_drafts(path, run_verifier=False)
    problems = [(r.entry.question.id, m) for r in reports for m in errors(r)]
    assert problems == []
