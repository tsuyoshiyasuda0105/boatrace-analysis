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

- No local process. Render owns all scheduled execution.

## Failures

- `poll_results.py` compared Render UTC `datetime.now()` with JST-naive race close times and used UTC `date.today()`; results could be delayed up to nine hours or target the previous date in the JST morning. Prevention: all result polling and integrity cutoffs now use explicit Asia/Tokyo time, covered by regression tests.
- GitHub CLI is installed but not authenticated. This does not block the repository's existing direct main push flow; no PR workflow is being used.
- After rebasing onto remote main, three pre-existing `test_scheduler_morning_order.py` assertions failed because they still expect the old daytime schedule/tag-prewarm structure. The JST result tests and result-target test pass. Prevention: keep this fix scoped; do not rewrite unrelated scheduler behavior during a result-ingestion repair, and report the upstream test drift separately.
- Render's HTTP client advertised Brotli without installing a Brotli decoder, so official result HTML could return unusable compressed content. Prevention: advertise only gzip/deflate and log target/fetched/failed counts.
- Result polling ran after long signal/detail prewarming. Prevention: poll and settle results before prewarm work in every race-hour run.

## Next actions

- Monitor the normal five-minute Render cycle; failed/not-yet-published result pages remain retryable.
- Update the three stale scheduler-order tests separately when that test suite is next maintained.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- Render production run at 16:31 JST: 22 targets, 20 fetched, 119 result rows upserted; the two initially unpublished races were filled by the next cycle.
- Supabase: 20 result races and 20 payout races immediately after recovery; active ROI ledger settled ended candidates.
- Public ROI page at 16:40 JST: 3 valid rows (Kiryu 12R active, Amagasaki 12R ended, Ashiya 12R ended).
- Targeted tests: 9 passed (`test_roi_history`, result timezone, market-signal targets).
