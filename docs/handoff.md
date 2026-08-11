# Handoff

## Active task

- 2026-08-11: Rin P0 race-detail HTML cache discrepancy resolved at `180/180`; finish accurate admin schedule labels and preserve the newly exposed motor-history issue as the next separate investigation.
- Skills: project-ops-guard, cron-watchdog, bug-resistant-programming.

## Expected files

- `src/web/app.py`
- `scripts/prewarm_race_detail_pages.py`
- `scripts/prewarm_race_detail_data.py`
- `scripts/check_post_run_integrity.py`
- `tests/test_admin_data_status.py`
- `tests/test_race_detail_page_prewarm.py`
- `tests/test_race_detail_data_schedule.py`
- `docs/handoff.md`

## Conflict avoidance

- Preserve unrelated user changes and inspect `git status` before editing.

## Running processes

- No local process. Production Playwright checks must close Chromium after each audit; Render owns all scheduled execution.

## Failures

- The first read-only Render Shell cache-count query used a literal SQL `LIKE` percent pattern, which the PostgreSQL adapter parsed as an invalid placeholder. Prevention: use `split_part`/`substring` for shell diagnostics or escape literal percent signs; the corrected read-only query completed without changing data.
- The first P0 race-detail cache regression command referenced a nonexistent `.venv` in the push-sync worktree. Prevention: use the verified shared runtime at `C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe` for this worktree and keep the working directory on the code under test.
- During the production motor click audit, the browser locator wait timed out even though its own diagnostics reported the inspector visible. A direct DOM read confirmed `aria-expanded=true`, a visible panel, 11 history rows, and no application error, so the action was not repeated. Prevention: after a contradictory locator timeout, inspect current DOM state before retrying an interaction.
- The first commit attempt could not create the linked-worktree `index.lock` under `C:\boat_project\boatrace-analysis\.git\worktrees` because that Git metadata is outside the writable workspace. Prevention: keep the same explicit file list and rerun only the Git add/commit operation with approved elevated filesystem access.
- The first focused regression bundle passed 68 tests and failed 8. One new motor-history test invalidated an LRU cache after monkeypatching the cached function, causing `cache_clear` to be unavailable; prevention: invalidate shared caches before replacing cached functions. The other failures are existing cross-test cache leakage, blocked external-network access, and stale source assertions outside this scoped change; they are being separated from the exact changed-path rerun.
- The related regression bundle initially had two source-assertion failures: one expected `_race_basic_info()` before the already-established page-cache-first path, and one expected the old unrestricted `{% if is_admin() %}` template condition. Prevention: assertions now preserve cache-first performance and require the cache-neutral admin guard.
- Production verification exposed a pre-existing shared-cache display bug: race-detail HTML included the prewarm session badge (`paid_member / none`) instead of the current viewer. Authorization remained server-enforced, but shared HTML must not contain viewer-specific role/provider or admin navigation. Prevention: render a cache-neutral member header for the race-detail endpoint and cover it with a regression test.
- The first cache-neutral header deploy still served persistent `v13` HTML generated before the template change. Prevention: bump the race-detail page cache generation whenever shared HTML structure changes; `v14` makes old HTML unreachable without deleting data.
- Production timing reproduced a 3.153s TOP load immediately after the 60-second Supabase role TTL expired, while subsequent loads were about 0.30s. Prevention: retain 60-second role revocation checks but remove the redundant profile upsert from refresh; profiles are still ensured during Supabase login and ordinary refresh performs only the role read.
- Read-only role refresh still measured 2.112s in steady state because every `db_connect()` opened a new PostgreSQL TLS connection. Prevention: use a process-local `psycopg_pool` for PostgreSQL while preserving the existing connection context API and all 60-second role checks; SQLite and schema behavior are unchanged.
- Standalone Playwright was redirected to `/login?next=/races` because the dedicated password is intentionally not stored in the local environment. Prevention: use an approved storage-state file or the logged-in Chrome session; never copy credentials from Render or browser storage.
- Chrome's isolated page evaluator does not expose `performance` or `fetch`, and direct `/healthz` navigation was blocked by a browser extension. Prevention: read the application's `topDiagnostics`/`raceDiagnostics` datasets and use unauthenticated terminal timing only for `/healthz` and static assets.
- `poll_results.py` compared Render UTC `datetime.now()` with JST-naive race close times and used UTC `date.today()`; results could be delayed up to nine hours or target the previous date in the JST morning. Prevention: all result polling and integrity cutoffs now use explicit Asia/Tokyo time, covered by regression tests.
- GitHub CLI is installed but not authenticated. This does not block the repository's existing direct main push flow; no PR workflow is being used.
- After rebasing onto remote main, three pre-existing `test_scheduler_morning_order.py` assertions failed because they still expect the old daytime schedule/tag-prewarm structure. The JST result tests and result-target test pass. Prevention: keep this fix scoped; do not rewrite unrelated scheduler behavior during a result-ingestion repair, and report the upstream test drift separately.
- Render's HTTP client advertised Brotli without installing a Brotli decoder, so official result HTML could return unusable compressed content. Prevention: advertise only gzip/deflate and log target/fetched/failed counts.
- Result polling ran after long signal/detail prewarming. Prevention: poll and settle results before prewarm work in every race-hour run.

## Next actions

- Investigate nine `motor_history_v9` payloads for Edogawa (stadium 7) that exist but have an empty `history` array. Do not delete or regenerate them until source availability and expected fallback behavior are confirmed.
- Confirm the next JST daytime cron records complete source counts and lets `render_lite_daytime_bootstrap` finish, or records `source_incomplete` without running downstream tag/page prewarm.
- Monitor Render pool health and TOP/race-detail latency; investigate only if repeated measurements regress beyond the 1.5-second target.
- Confirm failed or not-yet-published result pages are recovered by a later five-minute cycle and that ROI settlement remains consistent.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- P0 production repair completed at 22:14 JST: the latest race-detail cron generated only the 100 missing `v14` pages in 135.056 seconds; `succeeded=100`, `failed=0`, `persistent_missing=0`, and persistent cache reads were 0.288-0.435 seconds. The ordinary cron command was restored immediately after launch.
- Read-only and persisted integrity checks both report race-detail pages `180/180`, tags `180/180`, missing pages `0`, and missing tags `0`. The admin data-status page changed Race Detail HTML from abnormal to healthy with present `180` and missing `0`.
- `boatrace-web`, `boatrace-race-detail-cron`, and `boatrace-exhibition-detail-cron` now use commit `d726739`. Render schedules were corrected directly to `0 19 * * *` (04:00 JST daily) and `*/5 23,0-13 * * *` (08:00-22:59 JST every five minutes); the broad Blueprint sync was intentionally not used because it would also remove the existing temporary cron and create another service.
- The full morning integrity run exposed a separate issue after the HTML repair: nine Edogawa motor caches have `empty_history` although all 1080 keys exist. Detail rows and detail caches are healthy; this motor-content defect remains isolated for the next task.
- Final focused regression suite after admin schedule-label correction: 30 passed. Python compile and `git diff --check` passed.
- 2026-08-11 P0 root cause confirmed read-only in production: race-detail page counts were `v10=180`, `v13=173`, and current `v14=80`. `v13` was generated from 15:53-17:29 JST, `v14` from 18:29-21:50, while obsolete `v10` was still updated at 22:00. The current web cache generation therefore changed after the daily run, seven persistent `v13` writes were missed despite a successful summary, and an old cron deployment continued writing obsolete keys.
- Render service inventory confirmed deployment skew: `boatrace-web` updated 16 minutes earlier and `boatrace-regular-cron` five hours earlier, but `boatrace-exhibition-detail-cron` and `boatrace-race-detail-cron` had not updated for seven days. Blueprint `autoDeploy: true` did not keep these cron instances aligned in practice.
- P0 prevention now verifies every generated HTML key in PostgreSQL, retries only unpersisted pages once, fails the cron if any persistent key remains missing, supports bounded `--missing-only` repair, and isolates targeted exhibition checks from full-day `system_status` keys.
- Focused P0 regression suite: 29 passed (`test_race_detail_page_prewarm.py`, `test_race_detail_data_schedule.py`, `test_admin_data_status.py`). Python compile and `git diff --check` passed.
- Production deploy `45e45a9` is live. `/healthz` returned HTTP 200. Logged-in TOP loaded in 0.902s with 183 race links, visible accident/escape tags, zero application runtime errors, and zero market-signals requests.
- Production race detail `/race/20260811-19-10` had one post-deploy cold load of 2.725s, then three reloads of 0.177s/0.206s/0.198s. It rendered six racers and six motor buttons with no application console error. The first motor opened from precomputed cache with 11 history rows and no pending/error message.
- 2026-08-11 missing-data guard: TOP snapshot reads no longer run expensive badge hydration; motor-history cache misses return HTTP 202 with `Retry-After: 300` instead of synchronously rebuilding; complete motor caches still return HTTP 200 without a race-info query.
- Lite daytime bootstrap now rechecks races, all six entries, six tag-ready entry rows (racer, motor number, motor top-2 rate), and prediction coverage after morning recovery. Incomplete source data stops signal/tag/page prewarm and records `source_incomplete` for the next hourly recovery attempt.
- Missing-data and nearby TOP/race-detail/cron regression bundle: 63 passed. ROI/market-signal regression bundle: 28 passed. Python compile, JavaScript syntax, and `git diff --check` passed.
- 2026-08-11 P1 baseline, logged-in Chrome (`admin / supabase`): TOP initial median load 4.617s, reload median 1.908s; race detail initial median 4.240s, reload median 1.831s. Both pages had zero application console/runtime errors and no extra TOP market-signals request.
- Unauthenticated fixed-cost baseline: `/healthz` 105-255ms and static CSS 192-366ms. The shared HTML delay is therefore not a general Render network delay.
- Root cause: `_refresh_supabase_membership_session()` called `ensure_profile()` and `get_effective_role()` before every authenticated request. The scoped fix caches the confirmed role in the signed Flask session for 60 seconds; no DB/RLS/schema change.
- 2026-08-11 post-fix production measurement after one session warm-up: TOP initial median 1.442s and reload median 0.824s; race detail initial median 0.468s and reload median 0.185s. Both 1.5-second targets are met and application errors remained zero.
- Shared race-detail HTML now uses a cache-neutral member header so prewarmed pages cannot leak or misrepresent a viewer role/provider or expose admin-only navigation.
- Auth regression suite: 23 passed (`test_auth_phase_migration.py`, `test_playwright_password_login.py`, `test_supabase_auth_stripe_migration.py`).
- Related cache/auth/UI/DB regression bundle after the `v14` cache-generation, read-only role refresh, and PostgreSQL pool changes: 87 passed, including `test_db_connection_pool.py` and `test_smoke.py`. Local verification used `psycopg_pool 3.3.1`.
- Production `v14` verification: cached race detail loaded in 0.432s/0.213s/0.221s, rendered six racers, displayed generic `会員`, exposed zero admin links, and showed no server error. Motor M35 expanded (`aria-expanded=true`) and rendered history racer rows without an acquisition error.
- TOP verification before PostgreSQL pooling: warm loads were 0.315s/0.304s with 183 race links, zero runtime errors, zero market-signal requests, and `renderTodaysPicks.calls=0`; expired-role requests measured 3.153s and 2.112s and motivated connection reuse without changing the 60-second TTL.
- Production `89be1d6` verification: the TOP request after the 60-second role boundary loaded in 0.899s (previously 2.112s), with zero runtime errors, zero market-signal requests, and `renderTodaysPicks.calls=0`.
- Final race-detail runs loaded in 3.059s/0.211s/0.229s (median 0.229s); the first was a transient server outlier, while the 1.5-second median target remained met. All runs showed six racers, generic `会員`, zero admin links, and no server error. Motor history expanded and rendered 11 history rows without an acquisition error.
- Render production run at 16:31 JST: 22 targets, 20 fetched, 119 result rows upserted; the two initially unpublished races were filled by the next cycle.
- Supabase: 20 result races and 20 payout races immediately after recovery; active ROI ledger settled ended candidates.
- Public ROI page at 16:40 JST: 3 valid rows (Kiryu 12R active, Amagasaki 12R ended, Ashiya 12R ended).
- Targeted tests: 9 passed (`test_roi_history`, result timezone, market-signal targets).
