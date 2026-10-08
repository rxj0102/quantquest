from core import db
from core.skill_tree import load_tree
from core.skill_view import NodeView, build_view
from tests.conftest import add_question


def view(conn) -> dict[str, NodeView]:
    return {v.id: v for v in build_view(load_tree(), db.list_playable(conn))}


def test_every_node_is_listed_in_tree_order(trusted_conn) -> None:
    tree = load_tree()
    assert [v.id for v in build_view(tree, db.list_playable(trusted_conn))] == tree.order


def test_trusted_counts_statuses_and_gaps(trusted_conn) -> None:
    v = view(trusted_conn)
    assert {k: (n.trusted_count, n.min_trusted) for k, n in v.items()} == {
        "prob.counting": (3, 3), "prob.conditional": (7, 3), "prob.expectation": (3, 3),
        "stat.moments": (3, 3), "stat.estimation": (2, 3), "la.determinants": (3, 3),
        "la.eigen": (8, 3), "la.projections": (1, 3),
    }  # fmt: skip
    assert {k for k, n in v.items() if n.status == "unlocked"} == {
        "prob.counting",
        "la.determinants",
    }
    assert v["la.projections"].need_more == 2 and v["prob.counting"].need_more == 0
    assert v["stat.estimation"].blocked_by == ["stat.moments", "prob.conditional"] or set(
        v["stat.estimation"].blocked_by
    ) == {"stat.moments", "prob.conditional"}


def test_titles_prerequisites_and_track_weights_are_carried_through(trusted_conn) -> None:
    n = view(trusted_conn)["stat.estimation"]
    assert n.title == "Estimation and inference"
    assert n.prerequisites == ["stat.moments", "prob.conditional"]
    assert set(n.tracks) == {"behavioral_quant", "quant_research", "risk_quant"}


def test_only_trusted_questions_are_counted(trusted_conn) -> None:
    add_question(trusted_conn, "zzf-001", status="fresh", node="prob.counting")
    add_question(trusted_conn, "zzg-001", status="flagged", node="prob.counting")
    add_question(trusted_conn, "zzr-001", status="retired", node="prob.counting")
    add_question(trusted_conn, "zzx-001", status="trusted", node="prob.counting")
    assert view(trusted_conn)["prob.counting"].trusted_count == 4


def test_generated_questions_do_not_count_toward_the_curated_tree(trusted_conn) -> None:
    add_question(trusted_conn, "gen-001", source="generated", node="prob.counting")
    assert view(trusted_conn)["prob.counting"].trusted_count == 3


def test_progress_changes_the_view(trusted_conn) -> None:
    for qid in ("prob-001", "prob-004", "prob-005"):
        db.record_attempt(trusted_conn, qid, correct=True)
    v = view(trusted_conn)
    assert v["prob.counting"].status == "mastered" and v["prob.counting"].mastery == 1.0
    assert v["prob.conditional"].status == "unlocked"
