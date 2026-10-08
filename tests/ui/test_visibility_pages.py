"""No page ever renders a fresh, flagged or retired question, whatever the user does."""

import pytest

from core import interview, review
from core.scheduler import Rating
from tests.ui.helpers import USER, all_text, at_time, button, configure, has_button, run_page

MARKERS = ("MARK-FRESH", "MARK-FLAGGED", "MARK-RETIRED")


@pytest.fixture(autouse=True)
def _env(monkeypatch, hidden_db):
    configure(monkeypatch, hidden_db)


def assert_clean(text: str, where: str) -> None:
    for marker in MARKERS:
        assert marker not in text, f"{marker} leaked into {where}"
    for hidden_id in ("hfp-001", "hgp-001", "hrp-001"):
        assert hidden_id not in text, f"{hidden_id} leaked into {where}"


def test_skill_tree_page_shows_only_trusted_counts() -> None:
    at = run_page("skill_tree")
    text = all_text(at)
    assert_clean(text, "skill tree")
    captions = [c.value for c in at.caption]
    assert captions.count("3/3 trusted questions") == 4  # unchanged by the hidden questions
    assert captions.count("7/3 trusted questions") == 2  # prob.conditional, la.projections
    assert captions.count("8/3 trusted questions") == 2  # stat.estimation, la.eigen
    assert not [c for c in captions if "Needs" in c]


def test_practice_page_never_offers_a_hidden_question_across_a_whole_session(db_conn) -> None:
    # also give hidden questions due reviews: they must still stay out
    for hid in [r[0] for r in db_conn.execute("SELECT id FROM questions WHERE id LIKE 'h%'")]:
        db_conn.execute(
            "INSERT OR IGNORE INTO review_state VALUES (?, ?, 2.5, 1, 1, 0, "
            "'2020-01-01T00:00:00.000000Z', NULL)",
            (USER, hid),
        )
    db_conn.commit()
    at = run_page("practice")
    seen = 0
    while seen < 12:
        text = all_text(at)
        assert_clean(text, f"practice card {seen}")
        if "Session complete" in text or not at.text_input:
            break
        at.text_input[0].set_value("0")
        button(at, "Check answer").click()
        at = at.run()
        assert_clean(all_text(at), f"practice answer {seen}")
        button(at, "Again").click()
        at = at.run()
        seen += 1
    assert seen >= 5  # it really walked through the queue


def test_practice_text_card_path_is_clean_too(db_conn) -> None:
    review.record_review(db_conn, USER, "prob-001", Rating.GOOD, at_time(-86400 * 3))
    assert_clean(all_text(run_page("practice")), "practice with a review")


def test_interview_pages_never_show_hidden_questions(db_conn) -> None:
    at = run_page("interview")
    assert_clean(all_text(at), "interview start")
    button(at, "Start interview").click()
    at = at.run()
    assert_clean(all_text(at), "interview active")
    for _ in range(3):
        if has_button(at, "Submit answer"):
            at.text_input[0].set_value("0")
            button(at, "Submit answer").click()
            at = at.run()
            assert_clean(all_text(at), "interview after answer")
    button(at, "Finish now").click()
    at = at.run()
    text = all_text(at)
    assert "Results" in text
    assert_clean(text, "interview results")
    run = interview.latest_run(db_conn, USER)
    assert not set(run.question_ids) & {
        r[0] for r in db_conn.execute("SELECT id FROM questions WHERE id LIKE 'h%'")
    }


def test_a_question_flagged_during_an_interview_disappears_from_the_results(db_conn) -> None:
    at = run_page("interview")
    button(at, "Start interview").click()
    at = at.run()
    run = interview.latest_run(db_conn, USER)
    victim = run.question_ids[1]
    prompt = db_conn.execute("SELECT prompt_md FROM questions WHERE id = ?", (victim,)).fetchone()[
        0
    ]
    db_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = ?", (victim,))
    db_conn.commit()
    button(at, "Finish now").click()
    at = at.run()
    text = all_text(at)
    assert prompt.strip() not in text and "was withdrawn" in text
