"""Page behaviour through Streamlit's AppTest. The logic is tested elsewhere; this checks wiring."""

from datetime import timedelta

import pytest

from core import db, interview, progress, review
from core.scheduler import Rating
from core.skill_tree import load_tree
from tests.conftest import add_question
from tests.ui.helpers import (
    T0,
    USER,
    all_text,
    at_time,
    button,
    configure,
    has_button,
    run_page,
    set_clock,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch, ui_db):
    configure(monkeypatch, ui_db)


# --- skill tree -------------------------------------------------------------------------------------------


def test_skill_tree_shows_every_node_with_status_and_trusted_counts() -> None:
    at = run_page("skill_tree")
    assert not at.exception
    text = all_text(at)
    for title in (
        "Counting and basic probability",
        "Estimation and inference",
        "Projections and least squares",
    ):
        assert title in text
    assert "🔓 Unlocked" in text and "🔒 Locked" in text
    assert (
        "3/3 trusted questions" in text
        and "2/3 trusted questions" in text
        and "1/3 trusted questions" in text
    )
    assert "Needs 2 more trusted question(s)" in text  # la.projections
    assert "Locked until mastered: Counting and basic probability" in text


def test_skill_tree_shows_a_mastered_node_after_progress(db_conn) -> None:
    for qid in ("prob-001", "prob-004", "prob-005"):
        db.record_attempt(db_conn, qid, correct=True)
    text = all_text(run_page("skill_tree"))
    assert "✅ Mastered" in text


# --- practice -----------------------------------------------------------------------------------------------


def first_card(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def answer(at, typed: str):
    at.text_input[0].set_value(typed)
    button(at, "Check answer").click()
    return at.run()


def test_practice_starts_with_a_new_card_and_renders_latex_unchanged(db_conn) -> None:
    at = run_page("practice")
    assert not at.exception
    prompt = db.get_playable(db_conn, "prob-001").prompt_md
    assert any(
        m.value == prompt.strip() for m in at.markdown
    )  # raw $...$ goes to st.markdown (KaTeX)
    assert "$9$" in prompt
    assert "New question" in all_text(at) and "Card 1 of 6" in all_text(at)
    assert "Solution" not in all_text(at)  # not before answering


def test_a_correct_answer_shows_the_solution_and_all_four_ratings(db_conn) -> None:
    at = answer(run_page("practice"), "1/9")
    text = all_text(at)
    assert [s.value for s in at.success] == ["Correct"]
    assert "Solution" in text and "4/36" in text
    assert [b.label for b in at.button if b.label in ("Again", "Hard", "Good", "Easy")] == [
        "Again", "Hard", "Good", "Easy",
    ]  # fmt: skip


def test_a_wrong_answer_allows_only_again(db_conn) -> None:
    at = answer(run_page("practice"), "1/8")
    assert [e.value for e in at.error] == ["Not quite"]
    labels = [b.label for b in at.button]
    assert (
        "Again" in labels and "Good" not in labels and "Easy" not in labels and "Hard" not in labels
    )
    assert "Solution" in all_text(at)


def test_rating_records_the_review_attempt_xp_and_advances(db_conn) -> None:
    at = answer(run_page("practice"), "1/9")
    button(at, "Good").click()
    at = at.run()
    assert not at.exception and "Card 2 of 6" in all_text(at)
    state = review.get_state(db_conn, USER, "prob-001")
    assert state is not None and state.interval_days == 1 and state.due_at == T0 + timedelta(days=1)
    stats = db.get_playable(db_conn, "prob-001").solve_stats
    assert (stats.attempts, stats.correct) == (1, 1)
    assert progress.total_xp(db_conn, USER) == 3  # difficulty 1 x Good (3)
    at_value = db_conn.execute("SELECT at FROM xp_event").fetchone()[0]
    assert at_value == "2026-01-15T14:00:00.000000Z"  # the injected clock, stored as UTC


def test_unreadable_input_is_not_counted_as_an_answer(db_conn) -> None:
    at = answer(run_page("practice"), "__import__('os').system('true')")
    assert any("Couldn't read" in w.value for w in at.warning)
    assert not at.success and not at.error
    assert not has_button(at, "Good")
    assert db_conn.execute("SELECT COUNT(*) FROM review_state").fetchone()[0] == 0


def test_the_hint_matches_the_kind_of_answer_expected(db_conn) -> None:
    assert "three significant figures" in all_text(run_page("practice"))  # prob-001: 1/9
    # make a specific question the first card by giving it the most overdue review
    review.record_review(db_conn, USER, "linalg-001", Rating.GOOD, at_time(-86400 * 5))
    assert "needs the exact value" in all_text(run_page("practice"))  # linalg-001: answer 1
    review.record_review(db_conn, USER, "linalg-005", Rating.GOOD, at_time(-86400 * 9))
    assert "use * for products" in all_text(run_page("practice"))  # linalg-005: symbolic


def test_due_reviews_come_before_new_questions(db_conn) -> None:
    review.record_review(db_conn, USER, "linalg-005", Rating.GOOD, at_time(-86400 * 5))
    at = run_page("practice")
    text = all_text(at)
    assert "Review due" in text and "Card 1 of 6" in text  # 1 due + the 5 other root cards
    assert any("det" in m.value for m in at.markdown)  # the linalg-005 prompt


def test_text_answers_are_labelled_unverified_and_self_graded(db_conn) -> None:
    add_question(
        db_conn, "txt-001", answer_type="text", answer="Because of df.", difficulty=1,
        node="prob.counting", fmt="derivation", prompt="Explain n-1.",
    )  # fmt: skip
    # difficulty 1 and id order put prob-001 first; review it so the text card is next
    review.record_review(db_conn, USER, "prob-001", Rating.GOOD, T0)
    at = run_page("practice")
    for _ in range(3):  # walk to the text card
        if "Explain n-1." in all_text(at):
            break
        at.text_input[0].set_value("0")
        button(at, "Check answer").click()
        at = at.run()
        button(at, "Again").click()
        at = at.run()
    assert "Explain n-1." in all_text(at)
    button(at, "Show answer").click()
    at = at.run()
    assert any("Unverified" in i.value for i in at.info)
    assert {b.label for b in at.button} >= {"Again", "Hard", "Good", "Easy"}


def test_empty_state_when_nothing_is_trusted(tmp_path, monkeypatch) -> None:
    configure(monkeypatch, tmp_path / "empty.db")
    at = run_page("practice")
    assert not at.exception
    assert "Nothing is due" in all_text(at)


def test_session_complete_message(db_conn, monkeypatch) -> None:
    monkeypatch.setenv("QQ_SESSION_MAX_CARDS", "1")
    at = answer(run_page("practice"), "1/9")
    button(at, "Easy").click()
    at = at.run()
    assert any("Session complete" in s.value for s in at.success)
    assert has_button(at, "Start a new session")


def test_a_question_withdrawn_mid_session_is_skipped(db_conn) -> None:
    at = run_page("practice")
    db_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-001'")
    db_conn.commit()
    at = at.run()
    assert "prob-001" not in all_text(at) and "$9$" not in all_text(at)


# --- interview ----------------------------------------------------------------------------------------------


def start_interview():
    at = run_page("interview")
    button(at, "Start interview").click()
    return at.run()


def left(at) -> str:
    return next(m.value for m in at.metric if m.label == "Time left")


def test_start_interview_shows_a_countdown_from_the_stored_deadline() -> None:
    at = start_interview()
    assert not at.exception and left(at) == "20:00"
    assert "Question 1" in all_text(at) and "Finish now" in all_text(at)


def test_refresh_does_not_reset_the_countdown(db_conn, monkeypatch) -> None:
    at = start_interview()
    set_clock(monkeypatch, at_time(300))
    at = at.run()  # same session, five minutes later
    assert left(at) == "15:00"
    at = run_page("interview")  # a hard refresh is a new session
    assert left(at) == "15:00" and not has_button(at, "Start interview")
    assert db_conn.execute("SELECT COUNT(*) FROM interview_run").fetchone()[0] == 1


def test_a_second_tab_joins_the_same_run_with_the_same_deadline(db_conn, monkeypatch) -> None:
    tab_a = start_interview()
    set_clock(monkeypatch, at_time(600))
    tab_b = run_page("interview")
    assert left(tab_b) == "10:00" and not has_button(tab_b, "Start interview")
    run_ids = {r[0] for r in db_conn.execute("SELECT id FROM interview_run")}
    assert len(run_ids) == 1
    set_clock(monkeypatch, at_time(900))
    assert left(tab_a.run()) == "05:00" and left(run_page("interview")) == "05:00"
    assert interview.get_active_run(db_conn, USER, at_time(900)).deadline_at == at_time(1200)


def test_answering_then_finishing_shows_results(db_conn) -> None:
    at = start_interview()
    run = interview.get_active_run(db_conn, USER, T0)
    first = db.get_playable(db_conn, run.question_ids[0])
    at.text_input[0].set_value(first.answer)
    button(at, "Submit answer").click()
    at = at.run()
    assert "✔ Question 1" in all_text(at)  # answered; the page moves on to the next open question
    assert "Answered. Results are shown" not in all_text(at) or "Question 2" in all_text(at)
    button(at, "Finish now").click()
    at = at.run()
    text = all_text(at)
    assert "Results" in text and "1/5" in text and "XP earned" in text
    assert "Solution" not in text or first.solution_md in text
    assert not has_button(at, "Submit answer")


def test_time_running_out_ends_the_interview_with_results(db_conn, monkeypatch) -> None:
    at = start_interview()
    set_clock(monkeypatch, at_time(1200))
    at = at.run()
    text = all_text(at)
    assert "Time ran out." in text and "0/5" in text
    assert interview.get_run(db_conn, interview.latest_run(db_conn, USER).id).status == "expired"


def test_late_sessions_still_find_the_expired_results(db_conn, monkeypatch) -> None:
    start_interview()
    set_clock(monkeypatch, at_time(5000))
    at = run_page("interview")  # a tab opened long after the deadline
    assert has_button(at, "Start interview")
    assert any("Last interview" in e.label for e in at.expander)


def test_interview_xp_and_streak_show_up_in_progress(db_conn) -> None:
    at = start_interview()
    run = interview.get_active_run(db_conn, USER, T0)
    q = db.get_playable(db_conn, run.question_ids[0])
    at.text_input[0].set_value(q.answer)
    button(at, "Submit answer").click()
    at.run()
    from zoneinfo import ZoneInfo

    assert progress.total_xp(db_conn, USER) == q.difficulty * 3
    assert progress.streak(db_conn, USER, T0, ZoneInfo("America/New_York")).current == 1


def test_no_interview_without_unlocked_content(tmp_path, monkeypatch) -> None:
    configure(monkeypatch, tmp_path / "empty.db")
    at = run_page("interview")
    button(at, "Start interview").click()
    at = at.run()
    assert any("no eligible questions" in e.value for e in at.error)


def test_the_load_tree_import_is_the_shipped_tree() -> None:
    assert len(load_tree().nodes) == 8


def test_a_verdict_never_carries_over_to_a_different_card(db_conn) -> None:
    at = answer(run_page("practice"), "1/9")  # prob-001 answered, not yet rated
    assert has_button(at, "Good")
    db_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-001'")
    db_conn.commit()
    at = at.run()  # the answered question was withdrawn: the page moves to the next card
    assert not at.exception
    text = all_text(at)
    assert "Card 2 of 6" in text
    assert not has_button(at, "Good") and not has_button(at, "Easy")  # nothing to rate yet
    assert "Correct" not in [s.value for s in at.success]
    assert has_button(at, "Check answer")  # it is asking the new question
