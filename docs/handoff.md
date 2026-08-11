# Handoff

## Active task

- 2026-08-11: Rin continuation: verify production cron health and measure TOP/race-detail P1 performance against the 1.5-second target, then apply only a confirmed minimal fix.
- Skills: project-ops-guard, cron-watchdog, webapp-testing.

## Expected files

- `src/web/auth.py`
- `tests/test_auth_phase_migration.py`
- `docs/handoff.md`

## Conflict avoidance

- Preserve unrelated user changes and inspect `git status` before editing.

## Running processes

- No local process. Production Playwright checks must close Chromium after each audit; Render owns all scheduled execution.

## Failures

- Standalone Playwright was redirected to `/login?next=/races` because the dedicated password is intentionally not stored in the local environment. Prevention: use an approved storage-state file or the logged-in Chrome session; never copy credentials from Render or browser storage.
- Chrome's isolated page evaluator does not expose `performance` or `fetch`, and direct `/healthz` navigation was blocked by a browser extension. Prevention: read the application's `topDiagnostics`/`raceDiagnostics` datasets and use unauthenticated terminal timing only for `/healthz` and static assets.
- `poll_results.py` compared Render UTC `datetime.now()` with JST-naive race close times and used UTC `date.today()`; results could be delayed up to nine hours or target the previous date in the JST morning. Prevention: all result polling and integrity cutoffs now use explicit Asia/Tokyo time, covered by regression tests.
- GitHub CLI is installed but not authenticated. This does not block the repository's existing direct main push flow; no PR workflow is being used.
- After rebasing onto remote main, three pre-existing `test_scheduler_morning_order.py` assertions failed because they still expect the old daytime schedule/tag-prewarm structure. The JST result tests and result-target test pass. Prevention: keep this fix scoped; do not rewrite unrelated scheduler behavior during a result-ingestion repair, and report the upstream test drift separately.
- Render's HTTP client advertised Brotli without installing a Brotli decoder, so official result HTML could return unusable compressed content. Prevention: advertise only gzip/deflate and log target/fetched/failed counts.
- Result polling ran after long signal/detail prewarming. Prevention: poll and settle results before prewarm work in every race-hour run.

## Next actions

- Deploy the Supabase role-refresh TTL fix, then repeat TOP and race-detail initial/reload measurements after one warm-up request.
- Monitor the normal five-minute Render cycle; failed/not-yet-published result pages remain retryable.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- 2026-08-11 P1 baseline, logged-in Chrome (`admin / supabase`): TOP initial median load 4.617s, reload median 1.908s; race detail initial median 4.240s, reload median 1.831s. Both pages had zero application console/runtime errors and no extra TOP market-signals request.
- Unauthenticated fixed-cost baseline: `/healthz` 105-255ms and static CSS 192-366ms. The shared HTML delay is therefore not a general Render network delay.
- Root cause: `_refresh_supabase_membership_session()` called `ensure_profile()` and `get_effective_role()` before every authenticated request. The scoped fix caches the confirmed role in the signed Flask session for 60 seconds; no DB/RLS/schema change.
- Auth regression suite: 23 passed (`test_auth_phase_migration.py`, `test_playwright_password_login.py`, `test_supabase_auth_stripe_migration.py`).
- Render production run at 16:31 JST: 22 targets, 20 fetched, 119 result rows upserted; the two initially unpublished races were filled by the next cycle.
- Supabase: 20 result races and 20 payout races immediately after recovery; active ROI ledger settled ended candidates.
- Public ROI page at 16:40 JST: 3 valid rows (Kiryu 12R active, Amagasaki 12R ended, Ashiya 12R ended).
- Targeted tests: 9 passed (`test_roi_history`, result timezone, market-signal targets).
