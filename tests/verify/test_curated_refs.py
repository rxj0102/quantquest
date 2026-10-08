"""References carried in each curated YAML entry, and the trust rule for editing them."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from core import db
from core.loader import DEFAULT_DIR, LoadError, load_entries, load_questions
from core.schema import AnswerType, Status
from tests.pool import N_CURATED, N_TRUSTED
from verify.curated import load_curated, reference_hash
from verify.promote import main as promote_main
from verify.promote import promote_curated
from verify.references import CURATED_REFERENCES, ExactSpec, Reference

# --- loading ---------------------------------------------------------------------------------


def curated_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "curated"
    shutil.copytree(DEFAULT_DIR, dst)
    return dst


def edit_entry(directory: Path, qid: str, fn: Callable[[dict], None]) -> None:
    """Rewrite one YAML entry in a temporary copy of the curated files."""
    for path in directory.glob("*.yaml"):
        items = yaml.safe_load(path.read_text())
        for item in items:
            if item["id"] == qid:
                fn(item)
                path.write_text(
                    yaml.safe_dump(items, sort_keys=False, width=1000, allow_unicode=True)
                )
                return
    raise AssertionError(qid)


def sync(conn, directory: Path, registry: dict | None = None):
    """What bootstrap does: load entries, upsert with reference hashes, return references."""
    entries = load_curated(directory, registry={} if registry is None else registry)
    db.upsert_questions(
        conn, [e.question for e in entries], {e.question.id: e.reference_hash for e in entries}
    )
    return {e.question.id: e.reference for e in entries if e.reference is not None}


def test_loader_accepts_and_separates_the_reference_key() -> None:
    entries = load_entries()
    assert len(entries) == N_CURATED
    assert all("reference" not in e.question.model_dump() for e in entries)
    assert sum(e.reference is not None for e in entries) == N_TRUSTED
    assert [q.id for q in load_questions()] == [e.question.id for e in entries]  # API unchanged


@pytest.mark.parametrize("bad", ["not a mapping", ["list"], 7])
def test_non_mapping_reference_is_a_load_error(tmp_path: Path, bad: object) -> None:
    d = curated_copy(tmp_path)
    edit_entry(d, "prob-001", lambda item: item.update(reference=bad))
    with pytest.raises(LoadError, match=r"prob-001.*reference|reference.*prob-001"):
        load_curated(d, registry={})


def test_unknown_field_in_a_reference_names_the_file_and_question(tmp_path: Path) -> None:
    d = curated_copy(tmp_path)
    edit_entry(d, "prob-002", lambda item: item["reference"].update(evil="x"))
    with pytest.raises(LoadError, match=r"probability\.yaml.*prob-002"):
        load_curated(d, registry={})


def test_invalid_simulator_or_spec_values_are_load_errors(tmp_path: Path) -> None:
    d = curated_copy(tmp_path)
    edit_entry(
        d, "prob-001", lambda item: item["reference"]["exact"].update(symbols={"a": "weird"})
    )
    with pytest.raises(LoadError, match="prob-001"):
        load_curated(d, registry={})


# --- migration: YAML and registry agree --------------------------------------------------------


def test_every_non_text_curated_question_carries_its_reference_in_yaml() -> None:
    entries = {e.question.id: e for e in load_curated(registry={})}
    for qid, e in entries.items():
        if e.question.answer_type is AnswerType.TEXT:
            assert e.reference is None and e.source == "none", qid
        else:
            assert e.reference is not None and e.source == "yaml", qid


def test_yaml_references_equal_the_registry_entries() -> None:
    from_yaml = {e.question.id: e.reference for e in load_curated(registry={}) if e.reference}
    assert set(from_yaml) == set(CURATED_REFERENCES)
    for qid, ref in from_yaml.items():
        assert ref == CURATED_REFERENCES[qid], qid
        assert reference_hash(ref) == reference_hash(CURATED_REFERENCES[qid]), qid


def test_registry_still_works_as_a_fallback(tmp_path: Path) -> None:
    d = curated_copy(tmp_path)
    edit_entry(d, "prob-001", lambda item: item.pop("reference"))
    with_fallback = {e.question.id: e for e in load_curated(d)}
    assert with_fallback["prob-001"].source == "registry"
    assert with_fallback["prob-001"].reference == CURATED_REFERENCES["prob-001"]
    without = {e.question.id: e for e in load_curated(d, registry={})}
    assert without["prob-001"].reference is None and without["prob-001"].source == "none"


def test_yaml_and_registry_may_not_silently_disagree(tmp_path: Path) -> None:
    d = curated_copy(tmp_path)
    edit_entry(d, "prob-001", lambda item: item["reference"]["exact"].update(expr="1/8"))
    with pytest.raises(LoadError, match=r"prob-001.*registry"):
        load_curated(d)  # default registry present and different
    load_curated(d, registry={})  # fine once the registry is out of the picture


# --- the hash ------------------------------------------------------------------------------------


def test_hash_ignores_formatting_but_not_substance() -> None:
    a = Reference.model_validate({"exact": {"expr": "1/9"}, "rel_tol": 1e-9})
    b = Reference.model_validate({"rel_tol": 1e-9, "exact": {"symbols": {}, "expr": "1/9"}})
    c = Reference.model_validate({"exact": {"expr": "1/9"}})  # default rel_tol left implicit
    assert reference_hash(a) == reference_hash(b) == reference_hash(c)
    assert reference_hash(a) != reference_hash(Reference(exact=ExactSpec(expr="Rational(1, 9)")))
    assert reference_hash(a) != reference_hash(a.model_copy(update={"rel_tol": 1e-6}))
    assert reference_hash(None) == reference_hash(None) != reference_hash(a)
    assert len(reference_hash(a)) == 64


def test_hashes_are_stored_with_the_question(tmp_path: Path) -> None:
    conn = db.connect(":memory:")
    sync(conn, DEFAULT_DIR)
    got = dict(conn.execute("SELECT id, reference_hash FROM questions").fetchall())
    assert got["prob-001"] == reference_hash(CURATED_REFERENCES["prob-001"])
    assert got["stat-005"] == reference_hash(None)
    assert all(v is not None for v in got.values())


# --- (a) every curated question still verifies and stays trusted ---------------------------------


def test_a_every_curated_question_verifies_from_yaml_alone_and_is_trusted() -> None:
    conn = db.connect(":memory:")
    refs = sync(conn, DEFAULT_DIR)  # registry={}: the YAML is the only source
    report = promote_curated(conn, refs)
    assert len(report.promoted) == N_TRUSTED and report.flagged == []
    assert [u.question_id for u in report.unverified] == ["stat-005"]
    assert len(db.list_questions(conn, status="trusted")) == N_TRUSTED


def test_a_promotion_cli_uses_the_yaml_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert promote_main(["--db", str(tmp_path / "qq.db")]) == 0
    assert f"promoted {N_TRUSTED}" in capsys.readouterr().out.lower()


def test_a_a_legacy_database_keeps_everything_trusted_after_the_migration() -> None:
    """A database promoted by the old registry-only flow (no reference hashes yet)."""
    conn = db.connect(":memory:")
    db.upsert_questions(conn, load_questions())  # old flow: no hashes
    assert promote_curated(conn, CURATED_REFERENCES).flagged == []
    assert (
        conn.execute("SELECT COUNT(*) FROM questions WHERE reference_hash IS NOT NULL").fetchone()[
            0
        ]
        == 0
    )
    before = {q.id: q.verification for q in db.list_questions(conn, status="trusted")}
    sync(conn, DEFAULT_DIR)  # first reload with hashes: adopt them, do not reset
    assert {q.id: q.verification for q in db.list_questions(conn, status="trusted")} == before
    assert len(before) == N_TRUSTED
    assert (
        conn.execute("SELECT COUNT(*) FROM questions WHERE reference_hash IS NULL").fetchone()[0]
        == 0
    )


# --- (b) editing only the reference ---------------------------------------------------


@pytest.fixture
def trusted_world(tmp_path: Path):
    """A temp curated dir and a database where all curated are trusted."""
    d = curated_copy(tmp_path)
    conn = db.connect(":memory:")
    promote_curated(conn, sync(conn, d))
    assert len(db.list_questions(conn, status="trusted")) == N_TRUSTED
    yield d, conn
    conn.close()


def state(conn, qid: str):
    q = db.get_question(conn, qid, include_hidden=True)
    return q.status, q.verification


def test_b_cosmetic_reference_edits_keep_trust(trusted_world) -> None:
    d, conn = trusted_world
    before = state(conn, "prob-001")

    def reorder_and_make_defaults_explicit(item: dict) -> None:
        ref = item["reference"]
        item["reference"] = {"rel_tol": 1e-9, **dict(reversed(list(ref.items())))}
        item["reference"]["exact"] = {"symbols": ref["exact"].get("symbols", {}), **ref["exact"]}

    edit_entry(d, "prob-001", reorder_and_make_defaults_explicit)
    sync(conn, d)
    assert state(conn, "prob-001") == before and before[0] is Status.TRUSTED


def test_b_editing_only_the_solution_or_difficulty_keeps_trust(trusted_world) -> None:
    d, conn = trusted_world
    edit_entry(d, "prob-001", lambda i: i.update(solution_md="A clearer write-up.", difficulty=2))
    sync(conn, d)
    q = db.get_question(conn, "prob-001")
    assert q.status is Status.TRUSTED and q.solution_md == "A clearer write-up."


def test_b_a_substantive_reference_edit_resets_trust_then_reverification_restores_it(
    trusted_world,
) -> None:
    d, conn = trusted_world
    edit_entry(
        d,
        "prob-001",
        lambda i: i["reference"]["exact"].update(expr="Rational(4, 36)", symbols={}),
    )  # same value, different evidence
    refs = sync(conn, d)
    status, verification = state(conn, "prob-001")
    assert status is Status.FRESH and verification.result == "unverified"  # not preserved
    assert db.get_question(conn, "prob-001").status is Status.FRESH  # visible only once re-verified
    report = promote_curated(conn, refs)
    assert [o.question_id for o in report.promoted] == ["prob-001"]  # and nothing else re-ran
    status, verification = state(conn, "prob-001")
    assert status is Status.TRUSTED and verification.result == "pass"


def test_b_removing_a_reference_resets_trust_and_it_cannot_come_back(trusted_world) -> None:
    d, conn = trusted_world
    edit_entry(d, "stat-002", lambda i: i.pop("reference"))
    refs = sync(conn, d)  # registry={} : nothing to fall back on
    assert state(conn, "stat-002")[0] is Status.FRESH
    report = promote_curated(conn, refs)
    assert [u.question_id for u in report.unverified if u.question_id == "stat-002"] == ["stat-002"]
    assert state(conn, "stat-002")[0] is Status.FRESH


def test_b_a_caller_that_passes_no_hashes_does_not_disturb_trust(trusted_world) -> None:
    d, conn = trusted_world
    before = conn.execute("SELECT id, reference_hash, status FROM questions ORDER BY id").fetchall()
    db.upsert_questions(conn, load_questions(d))  # old-style call
    assert (
        conn.execute("SELECT id, reference_hash, status FROM questions ORDER BY id").fetchall()
        == before
    )


def test_b_unrelated_questions_are_untouched_by_one_reference_edit(trusted_world) -> None:
    d, conn = trusted_world
    edit_entry(
        d, "prob-001", lambda i: i["reference"]["exact"].update(expr="Rational(4, 36)", symbols={})
    )
    sync(conn, d)
    assert len(db.list_questions(conn, status="trusted")) == N_TRUSTED - 1


def test_b_solve_stats_and_created_at_survive_a_reference_reset(trusted_world) -> None:
    d, conn = trusted_world
    db.record_attempt(conn, "prob-001", correct=True)
    created = db.get_question(conn, "prob-001").created_at
    edit_entry(
        d, "prob-001", lambda i: i["reference"]["exact"].update(expr="Rational(4, 36)", symbols={})
    )
    sync(conn, d)
    q = db.get_question(conn, "prob-001")
    assert q.solve_stats.attempts == 1 and q.created_at == created


# --- (c) a wrong reference flags the question ---------------------------------------


def test_c_a_wrong_exact_reference_flags_the_question_and_hides_it(trusted_world) -> None:
    d, conn = trusted_world
    edit_entry(d, "prob-001", lambda i: i["reference"]["exact"].update(expr="1/8", symbols={}))
    report = promote_curated(conn, sync(conn, d))
    assert [f.question_id for f in report.flagged] == ["prob-001"]
    status, verification = state(conn, "prob-001")
    assert status is Status.FLAGGED and verification.result == "fail"
    assert db.get_question(conn, "prob-001") is None
    assert "prob-001" not in {q.id for q in db.list_questions(conn)}
    assert "prob-001" not in {q.id for q in db.list_questions(conn, status="trusted")}
    assert len(db.list_questions(conn, status="trusted")) == N_TRUSTED - 1


def test_c_a_wrong_simulator_reference_flags_even_though_the_exact_value_is_right(
    trusted_world,
) -> None:
    d, conn = trusted_world
    edit_entry(d, "prob-001", lambda i: i["reference"]["simulator"]["params"].update(target=8))
    report = promote_curated(conn, sync(conn, d))
    assert [f.question_id for f in report.flagged] == ["prob-001"]
    assert "monte_carlo" in report.flagged[0].method


def test_c_fixing_a_wrong_reference_unflags_it_through_reverification(trusted_world) -> None:
    d, conn = trusted_world
    edit_entry(d, "prob-001", lambda i: i["reference"]["exact"].update(expr="1/8", symbols={}))
    promote_curated(conn, sync(conn, d))
    assert state(conn, "prob-001")[0] is Status.FLAGGED
    sync(conn, d)  # unchanged wrong reference: stays flagged
    assert state(conn, "prob-001")[0] is Status.FLAGGED
    shutil.rmtree(d)
    shutil.copytree(DEFAULT_DIR, d)  # back to the correct reference
    refs = sync(conn, d)
    assert state(conn, "prob-001")[0] is Status.FRESH
    promote_curated(conn, refs)
    assert state(conn, "prob-001")[0] is Status.TRUSTED
