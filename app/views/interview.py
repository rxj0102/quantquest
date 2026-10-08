"""Timed interview page: a fixed set of questions against a countdown stored in the database."""

from __future__ import annotations

import streamlit as st

from app import runtime
from core import db, interview
from core.interview import Run, RunSummary
from core.skill_tree import load_tree


def _clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _results(summary: RunSummary, titles: dict[str, str]) -> None:
    st.header("Results")
    expired = summary.status == "expired"
    if expired:
        st.warning("Time ran out.")
    a, b, c, d = st.columns(4)
    a.metric("Score", f"{summary.correct}/{summary.total}")
    b.metric("Percent", f"{summary.score:.0%}")
    c.metric("Time used", _clock(summary.time_used_seconds))
    d.metric("XP earned", summary.xp_earned)
    st.caption(f"{summary.incorrect} wrong, {summary.unanswered} unanswered.")
    if summary.by_node:
        st.subheader("By topic")
        for node, (right, total) in summary.by_node.items():
            st.write(f"{titles.get(node, node)}: {right}/{total}")
    st.subheader("Questions")
    for number, item in enumerate(summary.items, start=1):
        if item.question is None:
            st.caption(f"Question {number} was withdrawn.")
            continue
        mark = {"correct": "✅", "incorrect": "❌", "unanswered": "➖"}[item.outcome]
        with st.expander(f"{mark} Question {number}"):
            st.markdown(item.question.prompt_md)
            st.write(f"Your answer: `{item.typed}`" if item.typed is not None else "Not answered.")
            st.markdown(f"**Answer:** `{item.question.answer}`")
            st.markdown(item.question.solution_md)


@st.fragment(run_every=1)
def _countdown(run_id: str) -> None:
    """Re-renders itself every second from the stored deadline; at zero it ends the interview."""
    with runtime.open_db(runtime.get_settings()) as conn:
        run = interview.get_run(conn, run_id)
        if run is None:
            return
        left = interview.remaining_seconds(run, runtime.utc_now())
    st.metric("Time left", _clock(left))
    if left == 0 or run.status != "active":
        st.rerun(scope="app")


def _active(conn, settings, run: Run) -> None:
    st.session_state.iv_current = run.id
    st.title("Interview in progress")
    _countdown(run.id)
    answered = {
        r[0]
        for r in conn.execute(
            "SELECT question_id FROM interview_answer WHERE run_id = ?", (run.id,)
        )
    }
    labels = [
        f"{'✔' if qid in answered else '○'} Question {i}"
        for i, qid in enumerate(run.question_ids, 1)
    ]
    first_open = next((i for i, qid in enumerate(run.question_ids) if qid not in answered), 0)
    choice = st.radio("Go to", labels, index=first_open, horizontal=True)
    qid = run.question_ids[labels.index(choice)]
    question = db.get_playable(conn, qid)
    if question is None:
        st.caption("This question was withdrawn.")
    else:
        st.markdown(question.prompt_md)
        if qid in answered:
            st.info("Answered. Results are shown when the interview ends.")
        else:
            with st.form(f"iv_{run.id}_{qid}"):
                typed = st.text_input("Your answer")
                go = st.form_submit_button("Submit answer")
            if go:
                try:
                    sub = interview.submit_answer(
                        conn,
                        run.id,
                        qid,
                        typed,
                        runtime.utc_now(),
                        ui_rel_tol=settings.ui_numeric_rel_tol,
                        timeout=settings.answer_timeout_seconds,
                    )
                except interview.DeadlinePassed:
                    st.rerun()
                except interview.AlreadyAnswered:
                    st.rerun()
                if sub.stored:
                    st.rerun()
                st.warning(sub.result.message)
    if st.button("Finish now"):
        interview.finish_run(conn, run.id, runtime.utc_now())
        st.rerun()  # the next run finds no active run and shows the results on their own


def page() -> None:
    """Render the interview page."""
    settings = runtime.get_settings()
    tree = load_tree()
    titles = {nid: n.title for nid, n in tree.nodes.items()}
    with runtime.open_db(settings) as conn:
        run = interview.get_active_run(conn, settings.user_id, runtime.utc_now())
        if run is not None:
            _active(conn, settings, run)
            return
        if "iv_current" in st.session_state:  # the run this session was showing has just ended
            st.session_state.iv_result = st.session_state.pop("iv_current")
        shown = st.session_state.get("iv_result")
        if shown:
            _results(interview.summarize(conn, shown), titles)
            if st.button("Start a new interview"):
                del st.session_state["iv_result"]
                st.rerun()
            return
        st.title("Timed interview")
        st.write(
            f"{settings.interview_questions} questions in {settings.interview_minutes} minutes, "
            "drawn from the topics you have unlocked. Answers are checked automatically."
        )
        last = interview.latest_run(conn, settings.user_id)
        if last is not None:
            with st.expander("Last interview"):
                _results(interview.summarize(conn, last.id), titles)
        if st.button("Start interview"):
            try:
                interview.start_run(
                    conn,
                    tree,
                    settings.user_id,
                    runtime.utc_now(),
                    n_questions=settings.interview_questions,
                    minutes=settings.interview_minutes,
                )
            except interview.InterviewUnavailable as exc:
                st.error(str(exc))
            else:
                st.rerun()
