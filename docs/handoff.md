# Handoff

## Active task

- 2026-08-11: Rin continuation: verify production cron health and measure TOP/race-detail P1 performance against the 1.5-second target, then apply only a confirmed minimal fix.
- Skills: project-ops-guard, cron-watchdog, webapp-testing.

## Expected files

- `src/web/auth.py`
- `tests/test_auth_phase_migration.py`
- `src/web/templates/base.html`
- `tests/test_race_detail_ui_facts.py`
- `docs/handoff.md`

## Conflict avoidance

- Preserve unrelated user changes and inspect `git status` before editing.

## Running processes

- No local process. Production Playwright checks must close Chromium after each audit; Render owns all scheduled execution.

## Failures

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

- After deploying PostgreSQL pooling, repeat a logged-in TOP request after the 60-second role boundary and confirm it remains below 1.5 seconds; also verify detail/motor history and Render health.
- Monitor the normal five-minute Render cycle; failed/not-yet-published result pages remain retryable.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- 2026-08-11 P1 baseline, logged-in Chrome (`admin / supabase`): TOP initial median load 4.617s, reload median 1.908s; race detail initial median 4.240s, reload median 1.831s. Both pages had zero application console/runtime errors and no extra TOP market-signals request.
- Unauthenticated fixed-cost baseline: `/healthz` 105-255ms and static CSS 192-366ms. The shared HTML delay is therefore not a general Render network delay.
- Root cause: `_refresh_supabase_membership_session()` called `ensure_profile()` and `get_effective_role()` before every authenticated request. The scoped fix caches the confirmed role in the signed Flask session for 60 seconds; no DB/RLS/schema change.
- 2026-08-11 post-fix production measurement after one session warm-up: TOP initial median 1.442s and reload median 0.824s; race detail initial median 0.468s and reload median 0.185s. Both 1.5-second targets are met and application errors remained zero.
- Shared race-detail HTML now uses a cache-neutral member header so prewarmed pages cannot leak or misrepresent a viewer role/provider or expose admin-only navigation.
- Auth regression suite: 23 passed (`test_auth_phase_migration.py`, `test_playwright_password_login.py`, `test_supabase_auth_stripe_migration.py`).
- Related cache/auth/UI/DB regression bundle after the `v14` cache-generation, read-only role refresh, and PostgreSQL pool changes: 87 passed, including `test_db_connection_pool.py` and `test_smoke.py`. Local verification used `psycopg_pool 3.3.1`.
- Production `v14` verification: cached race detail loaded in 0.432s/0.213s/0.221s, rendered six racers, displayed generic `会員`, exposed zero admin links, and showed no server error. Motor M35 expanded (`aria-expanded=true`) and rendered history racer rows without an acquisition error.
- Final TOP verification before the one-hour TTL deploy: warm loads were 0.315s/0.304s with 183 race links, zero runtime errors, zero market-signal requests, and `renderTodaysPicks.calls=0`; the reproduced 3.153s expired-role request motivated the final TTL change.
- Render production run at 16:31 JST: 22 targets, 20 fetched, 119 result rows upserted; the two initially unpublished races were filled by the next cycle.
- Supabase: 20 result races and 20 payout races immediately after recovery; active ROI ledger settled ended candidates.
- Public ROI page at 16:40 JST: 3 valid rows (Kiryu 12R active, Amagasaki 12R ended, Ashiya 12R ended).
- Targeted tests: 9 passed (`test_roi_history`, result timezone, market-signal targets).
