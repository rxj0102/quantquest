"""Skill tree: validation on load, mastery from trusted solve_stats, unlock logic."""

from pathlib import Path

import pytest
import yaml

from core import db
from core.loader import load_questions
from core.schema import Question, SolveStats, Status, Verification
from core.skill_tree import (
    TRACKS,
    TreeError,
    content_report,
    load_tree,
    node_mastery,
    node_states,
    parse_tree,
)
from tests.conftest import make_question


def tree_dict(*nodes: dict, **top: object) -> dict:
    return {"min_trusted_default": 3, "mastery_threshold_default": 0.6, **top, "nodes": list(nodes)}


def node(nid: str, prereqs: list[str] | None = None, **extra: object) -> dict:
    return {
        "id": nid,
        "title": nid,
        "tracks": {"behavioral_quant": 0.5, "quant_research": 0.5, "risk_quant": 0.5},
        "prerequisites": prereqs or [],
        **extra,
    }


def q(
    qid: str, node_id: str, *, status: str = "trusted", attempts: int = 0, correct: int = 0
) -> Question:
    data = make_question(
        id=qid,
        node_id=node_id,
        status=status,
        solve_stats={"attempts": attempts, "correct": correct},
    )
    if status == "trusted":
        data["verification"] = {"method": "t", "result": "pass"}
    return Question.model_validate(data)


# --- shipped tree ---------------------------------------------------------------------------


def test_shipped_tree_loads_with_eight_nodes() -> None:
    tree = load_tree()
    assert len(tree.nodes) == 8
    assert set(tree.nodes["stat.estimation"].prerequisites) == {"stat.moments", "prob.conditional"}
    assert tree.min_trusted_default == 3


def test_every_track_weight_present_and_in_range() -> None:
    for n in load_tree().nodes.values():
        assert set(n.tracks) == set(TRACKS) == {"behavioral_quant", "quant_research", "risk_quant"}
        assert all(0 <= w <= 1 for w in n.tracks.values())


def test_every_curated_question_node_exists(curated: list[Question]) -> None:
    tree = load_tree()
    tree.validate_questions(curated)  # does not raise
    assert {x.node_id for x in curated} <= set(tree.nodes)
    assert {x.node_id for x in curated} == set(tree.nodes)  # and every node has curated content


def test_unknown_question_node_id_is_reported(curated: list[Question]) -> None:
    bad = curated[0].model_copy(update={"node_id": "prob.nonexistent"})
    with pytest.raises(TreeError, match=rf"prob.nonexistent.*{bad.id}"):
        load_tree().validate_questions([bad, *curated[1:]])


def test_topological_order_puts_prerequisites_first() -> None:
    tree = load_tree()
    pos = {nid: i for i, nid in enumerate(tree.order)}
    for n in tree.nodes.values():
        assert all(pos[p] < pos[n.id] for p in n.prerequisites)


# --- validation on load ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,match",
    [
        (tree_dict(node("a.x", ["a.y"]), node("a.y", ["a.x"])), "cycle"),
        (tree_dict(node("a.x", ["a.x"])), "cycle|itself"),
        (tree_dict(node("a.x", ["a.y"]), node("a.y", ["a.z"]), node("a.z", ["a.x"])), "cycle"),
        (tree_dict(node("a.x", ["a.missing"])), "unknown prerequisite"),
        (tree_dict(node("a.x"), node("a.x")), "duplicate"),
        (tree_dict(), "at least one node"),
        (tree_dict({**node("a.x"), "tracks": {"behavioral_quant": 0.5}}), "tracks"),
        (
            tree_dict(
                node("a.x", tracks={"behavioral_quant": 1.5, "quant_research": 0, "risk_quant": 0})
            ),
            "tracks|1.5|less",
        ),
        (tree_dict(node("a.x", mastery_threshold=1.5)), "mastery_threshold|less"),
        (tree_dict(node("a.x", min_trusted=0)), "min_trusted|greater"),
        (tree_dict(node("Bad Id")), "id"),
        (tree_dict(node("a.x", ["a.y", "a.y"]), node("a.y")), "duplicate prerequisite"),
    ],
)
def test_invalid_trees_are_rejected(raw: dict, match: str) -> None:
    with pytest.raises(TreeError, match=match):
        parse_tree(raw)


def test_cycle_error_names_the_cycle() -> None:
    with pytest.raises(TreeError) as exc:
        parse_tree(tree_dict(node("a.x", ["a.y"]), node("a.y", ["a.x"])))
    assert "a.x" in str(exc.value) and "a.y" in str(exc.value)


def test_unreadable_files_raise_tree_error(tmp_path: Path) -> None:
    bad = tmp_path / "t.yaml"
    bad.write_text("nodes: [unclosed\n")
    with pytest.raises(TreeError):
        load_tree(bad)
    bad.write_text("- just a list\n")
    with pytest.raises(TreeError):
        load_tree(bad)


def test_tree_is_extensible_from_yaml_alone(tmp_path: Path) -> None:
    base = yaml.safe_load(Path("data/skill_tree.yaml").read_text())
    base["nodes"].append(node("opt.pricing", ["stat.estimation"]))
    path = tmp_path / "bigger.yaml"
    path.write_text(yaml.safe_dump(base))
    tree = load_tree(path)
    assert len(tree.nodes) == 9 and tree.order[-1] == "opt.pricing"


# --- mastery: trusted only, minimum count ---------------------------------------------------


def three_trusted(node_id: str = "a.x", attempts: int = 4, correct: int = 4) -> list[Question]:
    return [q(f"tq-{i:03d}", node_id, attempts=attempts, correct=correct) for i in range(1, 4)]


TREE = parse_tree(tree_dict(node("a.x"), node("a.y", ["a.x"]), node("a.z", ["a.y"])))


def test_mastery_formula_is_coverage_times_pooled_accuracy() -> None:
    qs = [
        q("tq-001", "a.x", attempts=4, correct=3),
        q("tq-002", "a.x", attempts=2, correct=2),
        q("tq-003", "a.x", attempts=4, correct=0),  # attempted, never correct: not covered
    ]
    m = node_mastery(TREE, "a.x", qs)
    assert m.trusted_count == 3 and m.covered == 2
    assert m.coverage == pytest.approx(2 / 3)
    assert m.accuracy == pytest.approx(5 / 10)
    assert m.mastery == pytest.approx(2 / 3 * 5 / 10)
    assert not m.mastered


def test_unattempted_questions_give_zero_mastery() -> None:
    m = node_mastery(TREE, "a.x", three_trusted(attempts=0, correct=0))
    assert m.mastery == 0 and m.accuracy == 0 and not m.mastered


@pytest.mark.parametrize("status", ["fresh", "flagged", "retired"])
def test_non_trusted_questions_never_count(status: str) -> None:
    perfect = [q(f"nq-{i:03d}", "a.x", status=status, attempts=50, correct=50) for i in range(1, 6)]
    m = node_mastery(TREE, "a.x", perfect)
    assert m.trusted_count == 0 and m.mastery == 0 and not m.mastered


def test_non_trusted_questions_do_not_dilute_or_pad_a_mixed_node() -> None:
    qs = [*three_trusted(), q("nq-001", "a.x", status="fresh", attempts=9, correct=0)]
    m = node_mastery(TREE, "a.x", qs)
    assert m.trusted_count == 3 and m.mastery == pytest.approx(1.0) and m.mastered


def test_minimum_trusted_count_blocks_mastery() -> None:
    two = three_trusted()[:2]
    m = node_mastery(TREE, "a.x", two)
    assert m.trusted_count == 2 and m.min_trusted == 3
    assert m.mastery == pytest.approx(1.0)  # score is high ...
    assert not m.enough_content and not m.mastered  # ... but not enough content to call it mastered
    assert node_mastery(TREE, "a.x", three_trusted()).mastered


def test_flagged_question_removes_a_node_from_mastered() -> None:
    qs = three_trusted()
    assert node_mastery(TREE, "a.x", qs).mastered
    qs[0] = qs[0].model_copy(update={"status": Status.FLAGGED})
    m = node_mastery(TREE, "a.x", qs)
    assert m.trusted_count == 2 and not m.mastered


def test_per_node_min_trusted_override() -> None:
    tree = parse_tree(tree_dict(node("a.x", min_trusted=1)))
    assert node_mastery(tree, "a.x", three_trusted()[:1]).mastered


def test_threshold_boundary_is_inclusive() -> None:
    tree = parse_tree(tree_dict(node("a.x"), mastery_threshold_default=0.6))
    at = [q(f"tq-{i:03d}", "a.x", attempts=10, correct=6) for i in range(1, 4)]
    just_below = [q(f"tq-{i:03d}", "a.x", attempts=1000, correct=599) for i in range(1, 4)]
    assert node_mastery(tree, "a.x", at).mastered
    assert not node_mastery(tree, "a.x", just_below).mastered


# --- unlock logic ---------------------------------------------------------------------------


def states(qs: list[Question]) -> dict[str, str]:
    return {nid: s.status for nid, s in node_states(TREE, qs).items()}


def test_roots_unlock_without_any_history() -> None:
    assert states([]) == {"a.x": "unlocked", "a.y": "locked", "a.z": "locked"}


def test_node_unlocks_only_when_all_prerequisites_are_mastered() -> None:
    qs = three_trusted("a.x")
    assert states(qs) == {"a.x": "mastered", "a.y": "unlocked", "a.z": "locked"}
    qs += three_trusted("a.y")[:0] + [
        q(f"uq-{i:03d}", "a.y", attempts=4, correct=4) for i in range(1, 4)
    ]
    assert states(qs) == {"a.x": "mastered", "a.y": "mastered", "a.z": "unlocked"}


def test_multiple_prerequisites_must_all_be_mastered() -> None:
    tree = parse_tree(tree_dict(node("a.p"), node("a.q"), node("a.c", ["a.p", "a.q"])))
    p = [q(f"pq-{i:03d}", "a.p", attempts=3, correct=3) for i in range(1, 4)]
    qq = [q(f"qq-{i:03d}", "a.q", attempts=3, correct=3) for i in range(1, 4)]
    assert node_states(tree, p)["a.c"].status == "locked"
    assert node_states(tree, qq)["a.c"].status == "locked"
    assert node_states(tree, p + qq)["a.c"].status == "unlocked"


def test_locked_status_lists_what_blocks_it() -> None:
    s = node_states(TREE, [])
    assert s["a.y"].blocked_by == ["a.x"] and s["a.x"].blocked_by == []


def test_flagging_a_prerequisite_question_relocks_dependents() -> None:
    qs = three_trusted("a.x") + [
        q(f"uq-{i:03d}", "a.y", attempts=4, correct=4) for i in range(1, 4)
    ]
    assert states(qs)["a.z"] == "unlocked"
    qs[0] = qs[0].model_copy(update={"status": Status.FLAGGED})  # a.x drops to 2 trusted
    assert states(qs) == {"a.x": "unlocked", "a.y": "locked", "a.z": "locked"}


def test_fresh_questions_cannot_unlock_anything() -> None:
    fresh = [q(f"fq-{i:03d}", "a.x", status="fresh", attempts=20, correct=20) for i in range(1, 6)]
    assert states(fresh) == {"a.x": "unlocked", "a.y": "locked", "a.z": "locked"}


# --- real curated pool ----------------------------------------------------------------------


def with_perfect_stats(qs: list[Question]) -> list[Question]:
    return [x.model_copy(update={"solve_stats": SolveStats(attempts=5, correct=5)}) for x in qs]


def test_curated_pool_with_no_history_unlocks_only_the_roots(trusted_conn) -> None:
    qs = db.list_questions(trusted_conn)
    s = {k: v.status for k, v in node_states(load_tree(), qs).items()}
    assert {k for k, v in s.items() if v == "unlocked"} == {"prob.counting", "la.determinants"}
    assert not any(v == "mastered" for v in s.values())


def test_curated_pool_content_gaps_are_visible_and_block_unlocks(trusted_conn) -> None:
    qs = with_perfect_stats(db.list_questions(trusted_conn))
    s = node_states(load_tree(), qs)
    # nodes with >= 3 trusted questions can be mastered ...
    for nid in (
        "prob.counting", "prob.conditional", "prob.expectation", "stat.moments", "la.determinants",
    ):  # fmt: skip
        assert s[nid].status == "mastered", nid
    # ... nodes with fewer cannot, however well the user does (they are content gaps)
    for nid, n in {
        "la.eigen": 2,
        "la.projections": 1,
        "stat.estimation": 2,
    }.items():
        assert s[nid].mastery.trusted_count == n and s[nid].status != "mastered", nid
    # both its prerequisites can now be mastered, so it is open, but it cannot itself be mastered
    assert s["stat.estimation"].status == "unlocked"


def test_content_report_lists_counts_and_gaps(trusted_conn) -> None:
    rep = content_report(load_tree(), db.list_questions(trusted_conn))
    counts = {r.node_id: r.trusted_count for r in rep.rows}
    assert counts == {
        "prob.counting": 3, "prob.conditional": 7, "prob.expectation": 3, "stat.moments": 3,
        "stat.estimation": 2, "la.determinants": 3, "la.eigen": 2, "la.projections": 1,
    }  # fmt: skip
    assert set(rep.below_minimum) == {
        "stat.estimation",
        "la.eigen",
        "la.projections",
    }
    assert rep.unreachable == []  # every prerequisite chain can now be mastered
    text = rep.format()
    for nid in counts:
        assert nid in text
    assert "2/3" in text and "1/3" in text and "need" in text.lower()


def test_content_report_when_everything_has_enough_content() -> None:
    qs = three_trusted("a.x") + three_trusted("a.y") + three_trusted("a.z")
    qs = [x.model_copy(update={"id": f"cq-{i:03d}"}) for i, x in enumerate(qs)]
    rep = content_report(TREE, qs)
    assert rep.below_minimum == [] and rep.unreachable == []
    assert "all nodes have" in rep.format().lower()


def test_cli_prints_counts_per_node(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], curated
) -> None:
    from core.skill_tree import main

    path = tmp_path / "qq.db"
    conn = db.connect(path)
    db.upsert_questions(conn, curated)
    for qq in curated:
        if qq.id in {"prob-001", "prob-004", "prob-005"}:
            db.set_verification(
                conn, qq.id, Verification(method="t", result="pass"), Status.TRUSTED
            )
    conn.close()
    assert main(["--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "prob.counting" in out and "3/3" in out and "prob.conditional" in out and "0/3" in out


def test_questions_loaded_from_yaml_all_have_nodes_in_the_tree() -> None:
    load_tree().validate_questions(load_questions())


def test_defaults_apply_when_the_yaml_omits_them() -> None:
    tree = parse_tree({"nodes": [node("a.x")]})
    assert tree.min_trusted_default == 3 and tree.mastery_threshold_default == 0.6
    assert tree.min_trusted("a.x") == 3 and tree.threshold("a.x") == 0.6


def test_node_overrides_beat_the_defaults() -> None:
    tree = parse_tree(tree_dict(node("a.x", min_trusted=5, mastery_threshold=0.9)))
    assert tree.min_trusted("a.x") == 5 and tree.threshold("a.x") == 0.9
