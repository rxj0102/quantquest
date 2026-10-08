"""The M3 node_id merges (12 -> 8 nodes) must not cost any question its trusted status."""

import pytest

from core import db
from core.loader import load_questions
from core.schema import Status
from core.skill_tree import TreeError, load_tree
from verify.promote import promote_curated
from verify.references import CURATED_REFERENCES

OLD_NODE_IDS = {
    "prob-008": "prob.random_walks",
    "stat-001": "stat.sampling",
    "stat-006": "stat.inference",
    "linalg-003": "la.rank",
}


def test_reloading_after_the_node_id_merge_leaves_all_19_trusted() -> None:
    current = load_questions()
    old_yaml = [
        q.model_copy(update={"node_id": OLD_NODE_IDS.get(q.id, q.node_id)}) for q in current
    ]

    conn = db.connect(":memory:")
    db.upsert_questions(conn, old_yaml)  # a database built before the merge
    report = promote_curated(conn, CURATED_REFERENCES)  # real verification
    assert len(report.promoted) == 19 and report.flagged == []
    db.record_attempt(conn, "prob-008", correct=True)
    verification_before = {q.id: q.verification for q in db.list_questions(conn, status="trusted")}
    created_before = {q.id: q.created_at for q in db.list_questions(conn)}

    # the old node ids are unknown to the new tree: this is why the merge needed a reload
    with pytest.raises(TreeError, match="prob.random_walks"):
        load_tree().validate_questions(db.list_questions(conn))

    db.upsert_questions(conn, current)  # reload the merged YAML

    trusted = db.list_questions(conn, status="trusted")
    assert len(trusted) == 19
    assert {q.id for q in db.list_questions(conn)} == {q.id for q in current}
    assert db.get_question(conn, "stat-005").status is Status.FRESH  # text answer, never trusted
    assert all(db.get_question(conn, qid).node_id != old for qid, old in OLD_NODE_IDS.items())
    assert db.get_question(conn, "prob-008").node_id == "prob.expectation"
    assert {q.id: q.verification for q in trusted} == verification_before  # records not reset
    assert {q.id: q.created_at for q in db.list_questions(conn)} == created_before
    assert db.get_question(conn, "prob-008").solve_stats.attempts == 1  # stats survived
    load_tree().validate_questions(db.list_questions(conn))  # every node_id now exists
    conn.close()


def test_no_curated_question_still_uses_a_pre_merge_node_id() -> None:
    used = {q.node_id for q in load_questions()}
    assert used.isdisjoint(OLD_NODE_IDS.values())
    assert len(used) == 8
