# Plan: explicit unverified-practice status

Status: **approved, not started.** Written 2026-10-08. The only related change already made is
commit `27ea271` ("a text-answer question can never be trusted"); everything below is still to do.

Goal: questions that cannot be auto-verified (text, rubric, behavioral) can be practised in the
app with a self-grade and a rubric, award XP, count toward streaks, and **never** count toward
mastery and are **never** promoted to `trusted`.

## Findings that shape the design

- Visibility is fail-closed today. The UI reads only `db.list_playable` and `db.get_playable`
  (trusted, curated). The scheduler (`next_due`, `unseen_trusted`), the interview builder,
  mastery (`node_states`) and `skill_view` all go through those or filter `status = 'trusted'`.
  `record_review` rejects anything not trusted.
- Gap, now closed by `27ea271`: the `Question` validator used to let a text question be marked
  `trusted` with no verification. Text answers can no longer be trusted at all.
- Consequence of that commit: the practice page's self-graded text card is unreachable until this
  status exists. `tests/ui/test_pages.py::test_text_answers_are_labelled_unverified_and_self_graded`
  is skipped with a pointer to this file and must be re-enabled here.
- `status` has a SQL `CHECK` constraint (`fresh, trusted, flagged, retired`). Adding a value
  needs a table rebuild.

## Schema change

- Add `practice` to `Status` and to the SQL `CHECK` constraint. Every existing
  `status = 'trusted'` query excludes it automatically.
- Add an optional `rubric` list to `Question`.
- Rules on a `practice` question (validator and loader):
  curated source only; `answer_type` text; 3 to 5 rubric items; no `reference`; never `trusted`.
- `generated` questions can never have `practice` status (CLAUDE.md rule 4, pools stay separate).
- Existing data: stat-005 is the only text question. After the owner's review it is converted to
  `practice` with a rubric (a data change, not made automatically).

## Migration (table rebuild) with a backup

Keyed on `PRAGMA user_version`. The migration runs inside the existing `connect()` / bootstrap
path, so two app starts at once must be safe.

1. **Backup first, fail closed.** Before touching a file database whose `user_version` is below
   the new version, copy it with the SQLite online backup API (consistent under WAL) to
   `<db>.bak-v<old>-<UTC timestamp>`. Verify the copy (`PRAGMA integrity_check`, row counts per
   table). If the backup fails or does not verify, **do not migrate**; surface an error. Backups
   are never deleted automatically and the path is logged.
2. **Rebuild** (SQLite's documented procedure), inside `BEGIN IMMEDIATE`, re-reading
   `user_version` after the lock so a concurrent starter becomes a no-op:
   foreign keys off, create `questions_new` with the new `CHECK`, copy all rows, compare row
   counts, drop the old table, rename, recreate indexes, `PRAGMA foreign_key_check`, set
   `user_version`, commit. Any exception rolls the whole transaction back.
3. `:memory:` databases skip the backup (nothing to lose) and migrate directly.

## Behavior

- **Mastery and unlocks**: untouched. `skill_tree` still counts trusted questions only.
- **Accessors**: `list_playable` and `get_playable` are unchanged (trusted only). A new
  `list_practice` / `get_practice` is the only way to read practice questions.
- **Promotion**: `promote_curated` ignores them; `set_verification` refuses `trusted` for them.
- **Scheduling**: reuse SM-2 with a separate `next_due_practice` query; the trusted `next_due`
  and `unseen_trusted` are unchanged.
- **XP: flat at 50 percent with a daily cap.**
  - Award = 50% of what a verified question of the same difficulty earns at a GOOD rating
    (`difficulty x 3`), rounded half up, minimum 1, **independent of the self-rating** so it
    cannot be gamed. The factor lives in `Settings` (`practice_xp_factor`, default 0.5).
  - Daily cap: `practice_xp_daily_cap` in `Settings`, default 30 XP per local day in the
    configured timezone (America/New_York). Past the cap the review is still recorded with
    `xp = 0`.
  - **Streaks count practice.** A day counts if it has any XP event, and a capped zero-XP
    practice event is still logged, so a streak is never lost to the cap.
- **Practice page**: practice cards appear after verified cards, capped per session
  (`session_max_practice`, default 3). Each card shows an "Unverified practice" badge; the flow is
  Show answer, then the rubric as a checklist and the solution, then self-grade.
- **Skill tree page**: per node, "N practice questions (not counted toward mastery)", shown
  separately from the trusted count.
- **Interview mode**: excludes practice questions.

## Tests

1. **Visibility matrix**: for every status, through every accessor (`list_playable`,
   `get_playable`, `next_due`, `unseen_trusted`, `build_queue`, the interview builder,
   `node_states`, `skill_view`, `promote_curated`), assert the exact intended visibility.
2. **Golden test**: adding N practice questions changes no mastery value, unlock state, trusted
   count or interview selection.
3. **Constraint tests**: the `CHECK` rejects unknown statuses; the validator rejects `trusted`
   for practice and for text; `generated` plus `practice` is rejected; a practice question with a
   `reference` is a loader error; promotion cannot turn practice into trusted.
4. **Migration against a populated copy**: build a populated v-old database (curated pool,
   non-zero `solve_stats`, `review_state`, `xp_event`, an interview run), **copy the file**,
   migrate the copy, and assert every row of every table is identical except the new constraint,
   `user_version` is bumped, and the app boots on the result.
5. **Failed migration leaves the original intact**: inject a failure at each step (after the
   copy, after the drop, before the rename, before the commit); assert the database is
   byte-for-byte equivalent to before (same table dumps, same `user_version`), the backup exists
   and verifies, the app reports a clear error, and a rerun without the fault succeeds.
6. **Backup tests**: a failed or corrupt backup aborts the migration with the original untouched;
   the backup file opens and matches the pre-migration contents.
7. **Concurrency**: two simultaneous starters migrate once; the second is a no-op; no corruption.
8. **XP and streak tests**: flat award independent of rating; the factor and minimum; the daily
   cap with an injected clock at the New York midnight boundary; capped events still count for
   the streak; trusted XP is unaffected.
9. **UI tests** (re-enable the skipped text-card test): badge, rubric checklist and self-grade
   shown; practice XP awarded and capped; mastery and unlock states unchanged after answering;
   practice never shown in interview mode.

## Milestones (one branch, stop for review at each)

1. Schema, validator and loader rules, with the migration, backup and failure-injection tests.
2. Accessors and promotion guards, with the visibility matrix and golden tests.
3. Practice-page flow, XP factor and cap, streak behavior, skill-tree counts, UI tests.
4. Convert stat-005 to `practice` with a rubric (after review).

## Open items

- The exact meaning of "flat at 50 percent" is taken as 50% of the GOOD-rating XP; confirm.
- Daily cap default of 30 XP and per-session default of 3 practice cards are proposals.
