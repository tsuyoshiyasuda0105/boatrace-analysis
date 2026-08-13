# Handoff

## Active task (2026-08-13 worktree consolidation)

- Execute only the safe, approved portions of Phase 0 through Phase 2 from `reports/worktree_consolidation_plan_20260813.md`.
- Preserve all existing worktrees, branches, user changes, and production configuration.
- Skill: project-ops-guard.

## Expected files (2026-08-13 worktree consolidation)

- `docs/handoff.md`
- `reports/worktree_consolidation_log_20260813.md`
- Source, configuration, tests, and reports already modified/untracked in worktrees #6, #7, and #8, committed in place to dedicated `rescue/*` branches.

## Conflict avoidance (2026-08-13 worktree consolidation)

- Do not edit the previously claimed application files directly; rescue their existing bytes without rewriting them.
- Do not include temporary files, caches, screenshots, or large images in rescue commits.
- Do not merge or push to `main`, alter/deploy Render or Supabase, change/run scheduled tasks, delete worktrees/branches/files, or overwrite existing user changes.

## Active task

- 2026-08-11: Restore correct high-ROI race output for today's races.
- Skills: project-ops-guard, cron-watchdog, supabase.

## Expected files

- `src/web/app.py`
- `src/roi_history.py`
- `src/collectors/result_scraper.py`
- `scripts/render_regular_scheduler.py`
- Targeted tests for changed modules.

## Conflict avoidance

- Preserve unrelated user changes and inspect `git status` before editing.

## Running processes

- No local process. Render owns all scheduled execution.
- 2026-08-13 consolidation: no local server, watcher, helper, scheduled task, or production writer was started.

## Failures

- 2026-08-13 consolidation: Phase 0 bundle creation failed because the sandbox could not create `C:\boat_project\backup-boatrace-all-20260813.bundle.lock`. No backup output was created. Prevention: obtain explicit permission for writes under `C:\boat_project`, create and verify the bundle and both task XML backups, then begin rescue commits.
- 2026-08-13 consolidation: one combined PowerShell `Test-Path` loop had an `EmptyPipeElement` parser error and made no changes. Prevention: use independent literal-path checks.
- 2026-08-13 consolidation: a combined commit-object check used unquoted `^{commit}` expressions, which PowerShell parsed as ScriptBlocks. It made no changes; all objects were subsequently verified with independently quoted `git rev-parse --verify` commands.
- `poll_results.py` compared Render UTC `datetime.now()` with JST-naive race close times and used UTC `date.today()`; results could be delayed up to nine hours or target the previous date in the JST morning. Prevention: all result polling and integrity cutoffs now use explicit Asia/Tokyo time, covered by regression tests.
- GitHub CLI is installed but not authenticated. This does not block the repository's existing direct main push flow; no PR workflow is being used.
- After rebasing onto remote main, three pre-existing `test_scheduler_morning_order.py` assertions failed because they still expect the old daytime schedule/tag-prewarm structure. The JST result tests and result-target test pass. Prevention: keep this fix scoped; do not rewrite unrelated scheduler behavior during a result-ingestion repair, and report the upstream test drift separately.
- Render's HTTP client advertised Brotli without installing a Brotli decoder, so official result HTML could return unusable compressed content. Prevention: advertise only gzip/deflate and log target/fetched/failed counts.
- Result polling ran after long signal/detail prewarming. Prevention: poll and settle results before prewarm work in every race-hour run.

## Next actions

- Phase 0 through Phase 2 are complete. Await separate approval for Phase 3 main update, Phase 4 scheduled-task reassignment/run, and Phase 5 deletion.
- Monitor the normal five-minute Render cycle; failed/not-yet-published result pages remain retryable.
- Update the three stale scheduler-order tests separately when that test suite is next maintained.

## Open decisions

- 2026-08-13 consolidation: user approval remains required before any scheduled-task reassignment/run, main integration, or deletion.
- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- 2026-08-13 consolidation: Phase 0 backups verified; rescue commits are `42ed768`, `800ba71`, `8c358ca`; Phase 2 branch `rescue/unmerged-commits-20260813` contains `d6fb8d5` and `1edacff`.
- Main remains at `dade455`, 55 commits behind `origin/main`; all nine worktrees remain registered; no task was changed/run; no push, deployment, deletion, reset, force push, or main merge occurred.
- Render production run at 16:31 JST: 22 targets, 20 fetched, 119 result rows upserted; the two initially unpublished races were filled by the next cycle.
- Supabase: 20 result races and 20 payout races immediately after recovery; active ROI ledger settled ended candidates.
- Public ROI page at 16:40 JST: 3 valid rows (Kiryu 12R active, Amagasaki 12R ended, Ashiya 12R ended).
- Targeted tests: 9 passed (`test_roi_history`, result timezone, market-signal targets).
