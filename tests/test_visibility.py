"""Fresh, flagged and retired questions must never reach a user, through any code path."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import db, interview, practice, review
from core.skill_tree import load_tree
from core.skill_view import build_view
from tests.conftest import add_question
from tests.pool import N_TRUSTED

APP = Path(__file__).resolve().parent.parent / "app"
T0 = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
USER = "local"
HIDDEN = {"fresh": "MARK-FRESH", "flagged": "MARK-FLAGGED", "retired": "MARK-RETIRED"}


@pytest.fixture
def world(trusted_conn):
    """The trusted pool plus one hidden question of each status in every open node."""
    for status, marker in HIDDEN.items():
        for node in ("prob.counting", "la.determinants"):
            add_question(
                trusted_conn,
                f"h{status[0]}{node[0]}-001",
                status=status,
                node=node,
                prompt=f"{marker} prompt",
                solution=f"{marker} solution",
                answer="7",
            )
    return trusted_conn


def texts(questions) -> str:
    return " ".join(f"{q.id} {q.prompt_md} {q.solution_md} {q.answer}" for q in questions)


def test_the_playable_pool_contains_trusted_questions_only(world) -> None:
    playable = db.list_playable(world)
    assert len(playable) == N_TRUSTED
    assert not any(m in texts(playable) for m in HIDDEN.values())
    assert db.get_playable(world, "hfp-001") is None
    assert db.get_playable(world, "prob-001").id == "prob-001"


def test_playable_excludes_other_pools_unless_asked(world) -> None:
    add_question(world, "gen-001", source="generated")
    assert "gen-001" not in {q.id for q in db.list_playable(world)}
    assert {q.id for q in db.list_playable(world, source_kind="generated")} == {"gen-001"}


def test_practice_queue_never_contains_a_hidden_question(world) -> None:
    tree = load_tree()
    review_ids = ["prob-001"]
    for qid in review_ids:
        review.record_review(world, USER, qid, practice.Rating.GOOD, T0 - timedelta(days=5))
    for hidden_id in [r[0] for r in world.execute("SELECT id FROM questions WHERE id LIKE 'h%'")]:
        world.execute(
            "INSERT OR IGNORE INTO review_state VALUES (?, ?, 2.5, 1, 1, 0, '2020-01-01T00:00:00.000000Z', NULL)",
            (USER, hidden_id),
        )
    world.commit()
    cards = practice.build_queue(world, tree, USER, T0, max_cards=100, max_new=100)
    assert cards and not any(m in texts([c.question for c in cards]) for m in HIDDEN.values())
    assert not {c.question.id for c in cards} & {
        r[0] for r in world.execute("SELECT id FROM questions WHERE id LIKE 'h%'")
    }


def test_interview_selection_never_contains_a_hidden_question(world) -> None:
    tree = load_tree()
    for seed in range(20):
        chosen = interview.select_questions(world, tree, USER, 99, seed=seed)
        assert chosen and not any(m in texts(chosen) for m in HIDDEN.values())


def test_interview_runs_and_summaries_never_contain_a_hidden_question(world) -> None:
    tree = load_tree()
    run = interview.start_run(world, tree, USER, T0, n_questions=99, minutes=20, seed=1)
    summary = interview.finish_run(world, run.id, T0 + timedelta(seconds=60))
    shown = texts([i.question for i in summary.items if i.question])
    assert not any(m in shown for m in HIDDEN.values())


def test_skill_tree_view_counts_exclude_hidden_questions(world) -> None:
    view = {v.id: v for v in build_view(load_tree(), db.list_playable(world))}
    assert view["prob.counting"].trusted_count == 3 and view["la.determinants"].trusted_count == 3


def test_a_trusted_question_that_is_later_flagged_disappears_everywhere(trusted_conn) -> None:
    tree = load_tree()
    run = interview.start_run(trusted_conn, tree, USER, T0, n_questions=99, minutes=20, seed=1)
    assert "prob-001" in run.question_ids
    trusted_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-001'")
    trusted_conn.commit()
    assert "prob-001" not in {
        c.question.id
        for c in practice.build_queue(trusted_conn, tree, USER, T0, max_cards=99, max_new=99)
    }
    assert "prob-001" not in {
        q.id for q in interview.select_questions(trusted_conn, tree, USER, 99, seed=1)
    }
    assert "prob-001" not in {q.id for q in db.list_playable(trusted_conn)}
    summary = interview.finish_run(trusted_conn, run.id, T0 + timedelta(seconds=5))
    assert "prob-001" not in {i.question.id for i in summary.items if i.question}


# --- static guard: pages must go through the trusted-only accessors ---------------------------------------

FORBIDDEN = [
    r"\blist_questions\b",
    r"\bget_question\b",
    r"\bquestion_from_row\b",
    r"\binclude_hidden\b",
    r"\bunseen_trusted\b",  # use practice.build_queue, which also filters pool and node
    r"FROM\s+questions",
    r"load_questions",
    r"load_entries",
]


def app_sources() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def test_there_is_an_app_to_guard() -> None:
    assert app_sources(), "app/ has no Python files yet"


@pytest.mark.parametrize("path", app_sources(), ids=lambda p: p.relative_to(APP).as_posix())
def test_pages_do_not_reach_around_the_trusted_accessors(path: Path) -> None:
    src = path.read_text()
    for pattern in FORBIDDEN:
        if path.name == "bootstrap.py" and pattern in (r"load_entries", r"load_questions"):
            continue  # bootstrap loads YAML into the database; it renders nothing
        assert not re.search(pattern, src), f"{path.name} uses {pattern}"
