# Handoff

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

- None.

## Failures

- `poll_results.py` compared Render UTC `datetime.now()` with JST-naive race close times and used UTC `date.today()`; results could be delayed up to nine hours or target the previous date in the JST morning. Prevention: all result polling and integrity cutoffs now use explicit Asia/Tokyo time, covered by regression tests.
- GitHub CLI is installed but not authenticated. This does not block the repository's existing direct main push flow; no PR workflow is being used.
- After rebasing onto remote main, three pre-existing `test_scheduler_morning_order.py` assertions failed because they still expect the old daytime schedule/tag-prewarm structure. The JST result tests and result-target test pass. Prevention: keep this fix scoped; do not rewrite unrelated scheduler behavior during a result-ingestion repair, and report the upstream test drift separately.

## Next actions

- Rebase the scoped commit onto the latest remote main.
- Deploy through the main branch and verify today's result rows and ROI settlement.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.
