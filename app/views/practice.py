"""Practice page: due reviews first, then new questions from unlocked nodes."""

from __future__ import annotations

import streamlit as st

from app import runtime
from core import db, practice
from core.practice import allowed_ratings
from core.scheduler import Rating
from core.schema import AnswerType, Question
from core.skill_tree import load_tree
from verify.answers import AnswerResult, check_typed_answer, effective_tolerance

RATING_LABEL = {
    Rating.AGAIN: "Again",
    Rating.HARD: "Hard",
    Rating.GOOD: "Good",
    Rating.EASY: "Easy",
}


def _hint(question: Question, ui_rel_tol: float) -> str:
    if question.answer_type is AnswerType.SYMBOLIC:
        return "Type an expression: use * for products and ** or ^ for powers."
    if effective_tolerance(question, ui_rel_tol) == 0.0:
        return "Type a number. This one needs the exact value."
    return "Type a number or fraction. Decimals are accepted to about three significant figures."


def _new_session(conn, settings) -> None:
    cards = practice.build_queue(
        conn,
        load_tree(),
        settings.user_id,
        runtime.utc_now(),
        max_cards=settings.session_max_cards,
        max_new=settings.session_max_new,
    )
    st.session_state.pr_queue = [(c.question.id, c.kind) for c in cards]
    st.session_state.pr_index = 0
    st.session_state.pr_result = None
    st.session_state.pr_done = 0
    st.session_state.pr_xp = 0


def _ask(question: Question, settings) -> None:
    """The answer form. Stores the result in session state when the answer is readable."""
    if question.answer_type is AnswerType.TEXT:
        st.text_area("Your answer (optional notes)", key=f"note_{question.id}")
        if st.button("Show answer", key=f"show_{question.id}"):
            st.session_state.pr_result = (question.id, check_typed_answer(question, ""))
            st.rerun()
        return
    with st.form(f"form_{question.id}"):
        typed = st.text_input(
            "Your answer",
            key=f"typed_{question.id}",
            help=_hint(question, settings.ui_numeric_rel_tol),
        )
        submitted = st.form_submit_button("Check answer")
    st.caption(_hint(question, settings.ui_numeric_rel_tol))
    if submitted:
        result = check_typed_answer(
            question,
            typed,
            ui_rel_tol=settings.ui_numeric_rel_tol,
            timeout=settings.answer_timeout_seconds,
        )
        if result.outcome == "unreadable":
            st.warning(result.message)
        else:
            st.session_state.pr_result = (question.id, result)
            st.rerun()


def _verdict(result: AnswerResult) -> None:
    if result.outcome == "correct":
        st.success("Correct")
    elif result.outcome == "incorrect":
        st.error("Not quite")
    else:
        st.info("Unverified: grade yourself")
        st.caption(result.message)


def _rate(conn, settings, question: Question, result: AnswerResult) -> None:
    st.write("How did that go?")
    options = allowed_ratings(result)
    for col, rating in zip(st.columns(len(options)), options, strict=True):
        if col.button(RATING_LABEL[rating], key=f"rate_{question.id}_{rating.value}"):
            out = practice.rate(
                conn, settings.user_id, question.id, rating, runtime.utc_now(), result
            )
            st.session_state.pr_index += 1
            st.session_state.pr_done += 1
            st.session_state.pr_xp += out.xp
            st.session_state.pr_result = None
            st.toast(f"+{out.xp} XP")
            st.rerun()


def page() -> None:
    """Render the practice page."""
    settings = runtime.get_settings()
    st.title("Practice")
    with runtime.open_db(settings) as conn:
        if "pr_queue" not in st.session_state:
            _new_session(conn, settings)
        queue = st.session_state.pr_queue
        index = st.session_state.pr_index
        # skip anything that stopped being trusted since the queue was built
        while index < len(queue) and db.get_playable(conn, queue[index][0]) is None:
            index += 1
            st.session_state.pr_index = index
        if not queue:
            st.info(
                "Nothing is due and no new questions are available. Master the unlocked "
                "nodes on the Skill tree page to open more."
            )
            if st.button("Check again"):
                _new_session(conn, settings)
                st.rerun()
            return
        if index >= len(queue):
            st.success(
                f"Session complete: {st.session_state.pr_done} reviewed, "
                f"+{st.session_state.pr_xp} XP."
            )
            if st.button("Start a new session"):
                _new_session(conn, settings)
                st.rerun()
            return
        qid, kind = queue[index]
        question = db.get_playable(conn, qid)
        assert question is not None
        st.progress(index / len(queue), text=f"Card {index + 1} of {len(queue)}")
        st.caption(
            f"{'Review due' if kind == 'due' else 'New question'} "
            f"· difficulty {question.difficulty}/5"
        )
        st.markdown(question.prompt_md)
        stored = st.session_state.pr_result  # (question id, result): never reuse across cards
        result: AnswerResult | None = stored[1] if stored and stored[0] == qid else None
        if result is None:
            _ask(question, settings)
            return
        _verdict(result)
        st.markdown(
            f"**Answer:** {question.answer}"
            if question.answer_type is AnswerType.TEXT
            else f"**Answer:** `{question.answer}`"
        )
        with st.container(border=True):
            st.markdown("**Solution**")
            st.markdown(question.solution_md)
        _rate(conn, settings, question, result)
