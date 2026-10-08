#!/usr/bin/env bash
# Mutation run for the M4 logic (typed-answer tolerance, XP and streaks, practice queue, interview
# runs, visibility, reference-hash rule, strict promotion tolerance, worker pool).
# Run it alone: it edits source files temporarily. See scripts/mutation/README.md.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
baseline tests/test_answers.py tests/test_progress.py tests/test_practice.py tests/test_interview.py \
  tests/test_visibility.py tests/test_skill_view.py tests/verify/test_curated_refs.py \
  tests/test_ui_tolerance_isolation.py tests/verify/test_timeout.py
# --- tolerance rules (answers)
M verify/answers.py 's/return 0.0 if is_integer_answer(question.answer) else ui_rel_tol/return ui_rel_tol/' tests/test_answers.py
M verify/answers.py 's/    if question.answer_tolerance is not None:/    if False:/' tests/test_answers.py
M verify/answers.py 's/abs_tol=0.0 if tolerance == 0.0 else 1e-12/abs_tol=1e-12/' tests/test_answers.py
M verify/answers.py 's/if res.outcome == "error":/if False:/' tests/test_answers.py
M verify/answers.py 's/return bool(getattr(value, "is_Integer", False))/return True/' tests/test_answers.py
# --- progress / streaks
M core/progress.py 's/return to_utc(at).astimezone(tz).date()/return to_utc(at).date()/' tests/test_progress.py
M core/progress.py 's/if last >= today - timedelta(days=1):/if last >= today:/' tests/test_progress.py
M core/progress.py 's/run = run + 1 if cur - prev == timedelta(days=1) else 1/run = run + 1/' tests/test_progress.py
M core/progress.py 's/XP_MULTIPLIER = {Rating.AGAIN: 1, Rating.HARD: 2, Rating.GOOD: 3, Rating.EASY: 4}/XP_MULTIPLIER = {Rating.AGAIN: 1, Rating.HARD: 2, Rating.GOOD: 3, Rating.EASY: 3}/' tests/test_progress.py
# --- practice
M core/practice.py 's/and q.node_id in states and states\[q.node_id\].status != "locked"/and q.node_id in states/' tests/test_practice.py
M core/practice.py 's/    fresh.sort(key=lambda q: (tree.order.index(q.node_id), q.difficulty, q.id))/    fresh.sort(key=lambda q: q.id)/' tests/test_practice.py
M core/practice.py 's/        return \[Rating.AGAIN\]/        return [Rating.AGAIN, Rating.GOOD]/' tests/test_practice.py
M core/practice.py 's/    cards = \[Card(q, "due") for q in due\[: max(0, max_cards)\]\]/    cards = [Card(q, "due") for q in due[:max(0, max_cards)]][::-1]/' tests/test_practice.py
M core/practice.py 's/        progress.insert_xp_event(c, user_id, question_id, "review", xp, now)/        pass/' tests/test_practice.py
# --- interview
M core/interview.py 's/    if run.status != "active" or now >= run.deadline_at:/    if run.status != "active":/' tests/test_interview.py
M core/interview.py 's/        if row is not None:\n/XX/;s/            conn.commit()\n            return _run(row)/XX/' tests/test_interview.py
M core/interview.py 's/"WHERE user_id = ? AND status = '"'"'active'"'"' AND deadline_at <= ?",/"WHERE user_id = ? AND status = '"'"'active'"'"' AND deadline_at < ?",/' tests/test_interview.py
M core/interview.py 's/        deadline = now + timedelta(minutes=minutes)/        deadline = to_utc(datetime.now(UTC)) + timedelta(minutes=minutes)/;s/^from datetime import datetime, timedelta/from datetime import UTC, datetime, timedelta/' tests/test_interview.py
M core/interview.py 's/            and states\[q.node_id\].status != "locked"/            and True/' tests/test_interview.py
M core/interview.py 's/    if _answered(conn, run_id, question_id):\n        raise AlreadyAnswered/XX/;s/    if question_id not in run.question_ids:/    if False:/' tests/test_interview.py
M core/interview.py 's/        progress.insert_xp_event(conn, run.user_id, question_id, "interview", xp, now)/        pass/' tests/test_interview.py
M core/db.py 's/CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_run/CREATE INDEX IF NOT EXISTS idx_one_active_run/' tests/test_interview.py
# --- visibility
M core/db.py "s/WHERE status = 'trusted' AND source_kind = ? ORDER BY id/WHERE status != 'retired' AND source_kind = ? ORDER BY id/" tests/test_visibility.py tests/test_practice.py
M core/db.py "s/WHERE id = ? AND status = 'trusted' AND source_kind = 'curated'/WHERE id = ?/" tests/test_visibility.py
M core/skill_view.py 's/    states = node_states(tree, playable)/    states = node_states(tree, [])/' tests/test_skill_view.py
# --- reference-hash rule and promotion strictness
M core/db.py 's/              AND questions.reference_hash != excluded.reference_hash))"""/              AND 1 = 0))"""/' tests/verify/test_curated_refs.py
M verify/sympy_check.py 's/^DEFAULT_REL_TOL = 1e-9/DEFAULT_REL_TOL = 1e-3/' tests/test_ui_tolerance_isolation.py
# --- worker pool
M verify/timeout.py 's/            self.kill()\n            raise CallTimeout/XX/;s/RECYCLE_AFTER = 200/RECYCLE_AFTER = 10**9/' tests/verify/test_timeout.py
summary
