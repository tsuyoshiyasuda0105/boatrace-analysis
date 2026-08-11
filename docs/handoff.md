# Handoff

## Active task

- 2026-08-12: Restore the empty "today races" UX. Production audit confirmed 2026-08-12 has 155 races and one ROI candidate, while selecting unpublished 2026-08-13 makes the shared navigation carry that future date into "today races" and show zero. Keep future-date source safety unchanged; fix only the navigation date and verify in a real browser.
- 2026-08-12: Rin cron defense redesign. Reconcile the agreed 23:30 official / 00:10 Open API / adaptive retry / 06:30 final recovery / 07:30 alert flow with Render production, then implement a dedicated fail-closed bootstrap scheduler, full-run exclusion, pre-generation validation, tests, deployment, and production verification.
- 2026-08-12 completion: P0 source-consistency guard is delivered. Code is on `main`, web is live, regular-cron has the same built artifact, production gates fail closed, and logged-in browser regression passed.
- 2026-08-12: Rin P0 source-consistency guard delivery. Render ephemeral cron recovery, versioned daily gate reuse, fail-closed downstream stopping, non-zero scheduler exit propagation, deployment, and production browser/cron verification.
- 2026-08-11: Rin P0 source-consistency guard phase 2. Run a read-only 2026-08-12 real-data audit and add an optional independent expected-race manifest. Keep all production writers and cron integrations disabled.
- 2026-08-11: Rin P0 source-consistency guard phase 1. Add a read-only audit that compares official B program data with Open API program data before any production integration. No DB/schema change, deletion, production write, cron launch, or deploy in this phase.
- 2026-08-11: Rin P0 race-detail HTML cache discrepancy resolved at `180/180`; finish accurate admin schedule labels and preserve the newly exposed motor-history issue as the next separate investigation.
- Skills: project-ops-guard, cron-watchdog, bug-resistant-programming.

## Expected files

- `src/web/templates/base.html`
- `tests/test_today_races_page.py`
- `docs/handoff.md`
- `scripts/render_program_bootstrap_scheduler.py` (new)
- `scripts/prewarm_race_detail_data.py`
- `scripts/backfill_official.py` (only if missing-venue filtering is required)
- `render.yaml`
- `tests/test_program_bootstrap_scheduler.py` (new)
- `tests/test_race_detail_data_schedule.py`
- `tests/test_render_cron_schedule.py` (new)
- `docs/render-cron-overview.md`
- `docs/handoff.md`
- `scripts/audit_program_source_consistency.py` (new)
- `tests/test_program_source_consistency.py` (new)
- `src/parsers/official_b.py`
- `tests/test_official_b_parser.py` (new)
- `docs/handoff.md`
- `src/web/app.py`
- `scripts/prewarm_race_detail_pages.py`
- `scripts/prewarm_race_detail_data.py`
- `scripts/check_post_run_integrity.py`
- `tests/test_admin_data_status.py`
- `tests/test_race_detail_page_prewarm.py`
- `tests/test_race_detail_data_schedule.py`
- `docs/handoff.md`

## Conflict avoidance

- The cron-defense task will not change DB schema, ROI strategy rules, odds selection rules, or delete existing Render services. It will reuse the canonical source gate and existing collectors, keep new orchestration isolated, and inspect `git status` before each commit.
- New audit files are isolated from existing collectors. The current phase will not edit `src/collectors/openapi.py`, `scripts/backfill_official.py`, scheduler code, or database schema until the audit output and tests are reviewed.
- Preserve unrelated user changes and inspect `git status` before editing.

## Running processes

- 2026-08-12 cron-defense redesign used no local scheduler, production writer, test server, or background process. Only finite pytest/compile commands were run.
- 2026-08-12 P0 gate Playwright server on port 5015 is stopped; no listener remains. The manifest subagent is closed. No local scheduler or production writer was started.
- Both source-consistency subagents are closed. `scripts/with_server.py` stopped the temporary Flask server and Chromium after the Playwright audit. No local process or scheduler is running.

## Cleanup targets

- `.tmp_cron_defense_unit/`, `.tmp_cron_defense_retry/`, `.tmp_cron_defense_related/`, `.tmp_cron_defense_final/`, `.tmp_cron_defense_persistence/`, `.tmp_cron_defense_delivery/` (repository-local pytest basetemp directories created by this task)
- `.gate_input/` (temporary isolated real-source gate inputs; no DB or production writes)
- `.tmp_manifest_gate/`, `.tmp_gate_core/`, `.tmp_gate_db/`, `.tmp_gate_integration/` (generated pytest temporary data)
- `.tmp_final_p0_gate/` (generated final pytest temporary data)
- `.audit_input/2026-08-12/` (temporary downloaded official/Open API/index inputs and normalized audit fixtures)
- `.audit_input/2026-08-11/` (temporary published-day comparison inputs and report)
- `.tmp_pytest_source_audit/` (generated pytest temporary data)
- `.tmp_pytest_source_related/` (generated pytest temporary data)
- `.tmp_final_source_audit/` (generated final pytest temporary data)
- `.tmp_phase2_tests/`, `.tmp_phase2_retry/`, `.tmp_phase2_related/` (generated pytest temporary data)
- `.tmp_phase2_ui_fix/`, `.tmp_phase2_ui_fix_retry/`, `.tmp_phase2_final/` (generated pytest temporary data)
- `.tmp_gate_tests/` (generated pytest temporary data)
- `.tmp_final_verification/` (generated pytest temporary data)
- `artifacts/playwright-source-audit/` (generated local screenshots and JSON report)
- These paths were generated by this task, resolve inside the repository, and are safe to remove after verification.

## Failures

- The 26-test today-races bundle passed 23 and failed three pre-existing stale cache/badge assertions already recorded by the cron-defense work. The exact navigation regression and JST default tests pass. Prevention: do not change unrelated cache or badge behavior for a one-line navigation fix.
- The cron-defense commit first failed because the linked-worktree Git metadata under `C:\boat_project\boatrace-analysis\.git\worktrees` is outside the writable workspace and could not create `index.lock`. Prevention: retain the explicit reviewed file list and rerun only add/commit with approved Git metadata access.
- The first 44-test cron-defense retry had one failure because `test_render_cron_schedule.py` matched a `fromService` reference to `boatrace-regular-cron` instead of the service definition. Prevention: schedule tests now anchor on the four-space service-name indentation.
- The first 132-test related run had one stale assertion expecting regular-cron to call `run_morning_catchup_if_needed()`. The dedicated bootstrap intentionally owns acquisition now. Prevention: the test asserts persisted source-gate success precedes tag/page generation and separately asserts the 06:30 recovery milestone.
- The full 69-test guard bundle has four pre-existing failures: one stale market-signal cache source assertion and three stale accident aggregation assertions/mocks. All 38 exact changed-path tests and the 94-test related delivery suite pass. Prevention: do not rewrite unrelated production behavior to satisfy stale source assertions; keep the four upstream drifts visible for their own task.
- A repeated focused test run hit `PermissionError` under the shared Windows `pytest-of-tsuyo` directory. Prevention: use a repository-local `--basetemp`; the exact 38 tests then passed.
- The local real-data gate has no production `DATABASE_URL`, so 2026-08-11 correctly stopped as `db_program_incomplete` despite both raw sources having 180 races. The 2026-08-12 Open API response was still unavailable and correctly returned `retry_wait`. Prevention: production completion must inspect the Render task result, not infer DB readiness from a local environment without credentials.
- Initial cleanup could not remove `.gate_input/ui-gate.db` because two Playwright `run_web.py --port 5015 --testing` children still held it after the wrapper ended. Prevention: identify exact command lines before stopping processes; only verified PIDs 36792 and 22568 were stopped, then every recorded temporary target was removed.
- The first handoff completion patch did not apply because the active-task lines changed its expected context. Root cause: one large patch mixed several distant sections. Prevention: locate headings with `rg` and apply small section-specific updates; no code or data was affected.
- The broad 93-test integration run passed 87 and failed six. One scoped failure was an old signal-refresh call-list expectation and was updated to mock the new gate; the other five are pre-existing stale cache/accident assertions or a sandbox-blocked external accident request. Prevention: rerun changed-path scheduler tests separately; all three passed without altering unrelated accident behavior.
- The first isolated DB fixture load failed before inserts with `ModuleNotFoundError: scripts.backfill_official`. Root cause: the second temporary helper also lacked the repository root on `sys.path`. Prevention: prepend the verified root in every subdirectory helper; the initialized isolated DB remained empty and the source gate correctly returned `db_program_incomplete`.
- The first isolated real-source gate runner stopped before any fetch with `ModuleNotFoundError: config`. Root cause: Python used the temporary script directory as `sys.path[0]`. Prevention: prepend the verified repository root before project imports; no DB or raw source write occurred in the failed attempt.
- The first live official-manifest check returned `http_error` because the workspace sandbox blocks outbound sockets. Root cause: local network policy, not the collector. Prevention: rerun the same read-only request with approved network access; it returned HTTP 200 and 15 venues/180 races without exposing response content.
- Two inline Python attempts to load the isolated Playwright SQLite fixture failed before execution because PowerShell split SQL `*` and stripped nested quotes. Root cause: complex native-command quoting, not parser or DB behavior. Prevention: use a small reviewable temporary loader inside the recorded cleanup directory instead of retrying opaque one-liners.
- `scripts/init_db.py --help` does not implement argparse help and initialized the repository's empty local SQLite schema instead. No production connection or race data write occurred. Prevention: never probe this script with `--help`; set both `DATABASE_URL=''` and an isolated `BOATRACE_DB_PATH` before invoking it for UI fixtures.
- The first parser delimiter fix restored all 180 races but parsed the fixed-width value `8100.00` as motor 810 / rate 0.00. Root cause: greedy number matching can produce plausible but wrong values even when row counts pass. Prevention: constrain percentages to 0.00-100.00, make adjacent number groups non-greedy, and retain exact regression rows for both `8100.00` and `49.51123`.
- The first isolated Playwright motor click produced `aria-expanded=true` and populated inspector HTML, but the panel remained invisible. Root cause: `normalizeRaceDetailLayout()` moved the inspector before `data-start-prediction` inside its closed `<details>`. Prevention: anchor insertion to `data-start-prediction-details` and assert that the old nested insertion path cannot return.
- The 2026-08-12 Open API programs URL returned HTTP 404 at 23:16 JST while the official B archive and official race index were already available. Root cause: source publication timing differs; tomorrow's Open API is not guaranteed before midnight. Prevention: classify this as `retry_wait`, use official B only for the preliminary snapshot, and do not publish cross-source-validated output until the later Open API comparison completes.
- The first focused source-audit test run could not create pytest temporary files under the restricted AppData temp root, so five tests passed and two fixture setups errored. Root cause: sandbox filesystem permissions, not application behavior. Prevention: rerun with a workspace-local `--basetemp`; the complete 14-test suite passed.
- The related 45-test source/detail bundle passed 38 and failed seven pre-existing source-string assertions for removed TOP JavaScript, old market-signal self-heal behavior, an old two-year ROI expression, and cache version `v11`. None imports or exercises the new audit module. Prevention: keep the P0 audit isolated, record this upstream test drift, and do not rewrite unrelated production behavior in this phase.
- The first environment-presence diagnostic used a PowerShell `foreach` expression directly before a pipeline and failed parsing. Prevention: collect the loop results in `$rows` before piping; the corrected check confirmed no Playwright password or web secret is stored locally.
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

- Deploy the today-navigation fix and verify that selecting unpublished 2026-08-13 no longer makes the "today races" button show zero; the button must return to the current JST date. Continue waiting for the official 2026-08-13 B file before generating future race rows.
- Deploy the cron-defense commit, sync the Render Blueprint only after reviewing its plan for unintended service deletion, set/verify the new bootstrap service database binding, and confirm the live schedules/commands match `render.yaml`.
- Production completion for the cron redesign requires: no five-minute full B-file retry loop; `task_runs` shows adaptive next-attempt times; DB remains 180/1080/1080; the 07:00-09:59 detail recovery window fails closed until sources are ready and then generates 180 complete pages exactly once; and the admin warning is correct at/after 07:30.
- No P0 delivery blocker remains. At the first 08:00 JST regular-cron run, confirm the already-deployed guard changes from `retry_wait` to `ready`/`ready_with_warning` after Open API publication; if it remains unavailable, downstream generation must remain stopped and the next five-minute cycle must retry.
- Completed delivery record: source guard, Render web/cron artifacts, production gate behavior, TOP/detail timing, tags, and motor expansion are all verified below.
- Delivery completion criteria: deployed `main` contains the gate commit; `/healthz` is HTTP 200; source gate is `ready`/`ready_with_warning` or a correctly classified temporary `retry_wait`; incomplete data does not reach prediction/ROI/tag/page generation; TOP/detail median targets remain at or below 1.5 seconds; browser runtime errors are zero; no local scheduler or production writer is left running.
- Resolved deployment blocker: Render ephemeral raw inputs are now recovered inside the gate, one versioned daily success is persisted through `task_runs`, and gate failure stops downstream work with a non-zero scheduler exit.
- P0 source guard is delivered. Production confirmed both a complete `ready_with_warning` day and an unpublished-day `retry_wait` without downstream generation.
- Investigate nine `motor_history_v9` payloads for Edogawa (stadium 7) that exist but have an empty `history` array. Do not delete or regenerate them until source availability and expected fallback behavior are confirmed.
- Confirm the next JST daytime cron records complete source counts and lets `render_lite_daytime_bootstrap` finish, or records `source_incomplete` without running downstream tag/page prewarm.
- Monitor Render pool health and TOP/race-detail latency; investigate only if repeated measurements regress beyond the 1.5-second target.
- Confirm failed or not-yet-published result pages are recovered by a later five-minute cycle and that ROI settlement remains consistent.

## Open decisions

- Do not manufacture non-win candidates: 2026-08-11 has no non-win strategy that passed final conditions; recent days confirm those strategies still run and appear when matched.

## Verification

- 2026-08-12 cron-defense pre-deploy verification: 134 related scheduler/source/parser/detail/admin/smoke tests passed. Python compilation, `render.yaml` parsing, and `git diff --check` passed. SQLite persistence covered task state and admin warning writes; source retry coverage verified only missing stadiums are written.
- Production delivery: `main` contains `220cdb3` and hardening commit `305047b`. Render web deploy `dep-d9tk4gijobas73d715g0` is live on `305047b`; `boatrace-regular-cron` manual build succeeded on the same commit without triggering an out-of-hours scheduler run. `/healthz` returned HTTP 200 and Render health checks remained HTTP 200 through cutover.
- Production source gate on 2026-08-11 returned `ready_with_warning`: official 180, Open API 180, manifest expected 180, DB races 180, entries 1080, detail entries 1080, zero omissions/incomplete boats/required-field gaps. The 23 warnings are the previously verified deadline revisions. On 2026-08-12 at 00:44 JST, official and manifest each had 180 while Open API was unavailable, so the gate correctly returned `retry_wait` and did not allow downstream materialization.
- Logged-in production browser regression on 2026-08-12: TOP warm reloads were 389/342/314 ms (median 342 ms); race detail reloads were 310/244/299 ms (median 299 ms); motor inspector opened in 291 ms with `aria-expanded=true`, a visible panel, and 11 history rows. TOP diagnostics reported TTFB 174 ms/load 348 ms; race-detail diagnostics reported TTFB 105 ms/load 220 ms. Browser console errors were zero.
- TOP badge verification for 2026-08-11 found 64 accident badges, eight ace-motor badges, and 65 escape badges. Entry-change badges were zero for that finalized snapshot; no placeholder badge was manufactured.
- Pre-deploy delivery verification: 38/38 exact gate/scheduler tests passed; the broader related parser/audit/gate/race-detail/scheduler suite passed 94/94; Python compilation and `git diff --check` passed. The guard recovers missing ephemeral official/Open API inputs, records one versioned daily success, stops tags/pages/ROI when the gate fails, and propagates scheduler failure through its exit code.
- 2026-08-12 pre-publication audit at 23:16 JST: official index expected 15 venues/180 races; the original B parser returned 13 venues/155 races because 145 fixed-width boat rows were rejected. Root cause was missing spaces where a 100.00 rate or three-digit boat number touched the neighboring column. The scoped parser fix now returns 15 venues, 180 races, 1080 boats, zero incomplete rows, and zero required racer/motor gaps.
- Published 2026-08-11 comparison: official B and Open API both contained the same 15 venues, 180 races, 1080 boats, racer numbers, and motor numbers. All 23 differences were deadlines: venue 3 had 12 revised times and venue 8 had 11 one-to-two-minute revisions. Direct official racelist pages matched Open API, so deadlines must use the newer Open API/official-web value while B remains the preliminary source.
- P0 source-consistency phase 1 added a read-only CLI that reports empty sources, venue/race omissions, incomplete or duplicate boat slots, required racer/motor gaps, target-date errors, natural-key/race-id errors, and cross-source racer/motor/deadline mismatches. It performs no network, DB, cache, cron, or production write.
- Source-audit tests: 14 passed. Related existing source/detail bundle: 38 passed and seven unrelated stale source-string assertions failed as recorded above. Python compile, CLI help, and `git diff --check` passed.
- Local Playwright testing-mode audit rendered TOP, member today-races, and admin data-status with no application warning or server error. Screenshots were visually inspected; the empty isolated SQLite database correctly showed no race cards, so race-detail navigation was skipped. `DATABASE_URL` was blank, and `with_server.py` closed Flask and Chromium.
- P0 production repair completed at 22:14 JST: the latest race-detail cron generated only the 100 missing `v14` pages in 135.056 seconds; `succeeded=100`, `failed=0`, `persistent_missing=0`, and persistent cache reads were 0.288-0.435 seconds. The ordinary cron command was restored immediately after launch.
- Read-only and persisted integrity checks both report race-detail pages `180/180`, tags `180/180`, missing pages `0`, and missing tags `0`. The admin data-status page changed Race Detail HTML from abnormal to healthy with present `180` and missing `0`.
- `boatrace-race-detail-cron` and `boatrace-exhibition-detail-cron` use repair commit `d726739`; `boatrace-web` uses label-correction commit `ac46a7f`. Render schedules were corrected directly to `0 19 * * *` (04:00 JST daily) and `*/5 23,0-13 * * *` (08:00-22:59 JST every five minutes); the broad Blueprint sync was intentionally not used because it would also remove the existing temporary cron and create another service.
- Public admin verification after `ac46a7f`: Race Detail HTML is healthy, present `180`, missing `0`, full-day status says `OK 180 races`, and both corrected schedule labels are visible. Render health checks returned HTTP 200 during deployment.
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
- P0 source guard phase 2 now accepts an independent official-index manifest and detects omissions even when both compared payloads omit the same venue or race. It validates the target date, venue/race ranges, race natural keys, six unique boat slots, racer/motor presence, and cross-source mismatches without any DB or network write.
- Source-gate classification separates `ready`, `ready_with_warning`, `retry_wait`, and `blocked`: an unpublished/unavailable Open API waits for retry, an empty official-B source blocks, deadline-only revisions continue with a warning, and an absent independent manifest cannot pass. The gate is now connected before morning, signal/ROI, and tomorrow nightly generation.
- Isolated real-data UI fixture for 2026-08-12 contained 15 venues, 180 races, and 1080 entries. Playwright rendered TOP with 15 venue cards/180 race links and race detail with six racers/motors, HTTP 200, no page error, and a 0.052-second server build.
- Playwright motor regression after the DOM-anchor fix: six motor buttons were present; clicking the first set `aria-expanded=true`; the inspector was visible under `MAIN` with a non-zero 1232x130 rectangle; the expected HTTP 202 missing-cache message rendered; browser page errors were zero.
- Final related regression bundle: 75 passed (`test_race_detail_ui_facts`, official-B parser/download, source consistency, race-detail scheduling, smoke). JavaScript syntax and `git diff --check` also passed.
- Gate-focused regression bundle: 51 passed. Python compile, JavaScript syntax, and `git diff --check` passed after the classification addition.
- Final combined regression bundle after all changes: 79 passed. JavaScript syntax and `git diff --check` passed.
- Independent official-index verification on 2026-08-12 returned HTTP 200, 15 venues, and 180 expected races. The parser uses `hd/jcd/rno` query values and expands each active venue to races 1-12 without depending on visible labels.
- Isolated published-day gate verification for 2026-08-11: official B 180, Open API 180, manifest 180, and DB 180 races/1080 entries/1080 detail-ready entries. The gate returned `ready_with_warning`; all 23 warnings were confirmed deadline revisions, with zero missing, incomplete, duplicate, or required-field issues.
- Isolated next-day verification for 2026-08-12 returned `retry_wait` while Open API was unavailable, despite official B and manifest each containing 180 races. Preliminary data therefore cannot reach prediction, ROI, tags, or page generation and the next cron can retry.
- The gate also blocks complete raw inputs when persisted DB counts do not exactly match expected races and six detail-ready entries per race.
- Playwright on the isolated 2026-08-11 DB rendered TOP with 15 venue cards/180 links and race detail with six boats. Both returned HTTP 200 without system warnings; race-detail server build was 0.069 seconds. Screenshots were visually inspected.
- Final P0 gate regression: 131 related tests plus three scheduler integration selections passed. Python compile, JavaScript syntax, and `git diff --check` passed.
- Cleanup complete: `.gate_input` and all P0 pytest temporary directories were removed. No port 5015 listener, local scheduler, test server, or production writer remains.
- 2026-08-12 architecture re-audit found and closed a late-publication gap: a source gate that became ready after the former one-shot 06:45 detail run could leave detail caches incomplete for the day. `boatrace-race-detail-cron` now runs every 15 minutes from 07:00 through 09:59 JST, skips after the first daily success, and skips a run already active within 30 minutes.
- `/api/market-signals` no longer has a second eight-second Flask response cache over its persisted snapshot cache. This removes stale double-caching without adding a DB query or changing the snapshot contract.
- Expanded architecture regression: 137 passed across program bootstrap, source consistency/gate, cron schedules, TOP/detail prewarm, market signals, accident status, and admin status. Python compilation, seven-service `render.yaml` parsing, and `git diff --check` passed. The only warning was Windows pytest cache-directory creation; no product test failed.
- Commit retry note: the first non-elevated `git add`/`git commit` could not create the linked-worktree `index.lock` under `C:/boat_project/boatrace-analysis/.git/worktrees`. No repository content changed; use the approved repository-scoped elevated Git command for this linked worktree rather than retrying ordinary Git writes.
- First production bootstrap run at 07:25 JST loaded commit `97483a7`, acquired official B successfully, classified unpublished Open API as `waiting`, and did not open the downstream gate. The old return policy nevertheless emitted exit 1 for this expected state; the follow-up changes waiting ticks to exit 0 while preserving `task_runs` retry state and the 07:30 admin warning.
- Blueprint sync plan displayed no deletion, but the sync removed the obsolete `boatrace-temp-race-detail-tags-cron`; the remaining seven services include the two new dedicated crons. The temporary service was not a production dependency, but this Render plan/display discrepancy must be considered before future syncs.
- Waiting-exit regression: 16 focused bootstrap/schedule tests passed, Python compilation and `git diff --check` passed. A broader rerun initially hit the inaccessible shared Windows pytest temp directory and an unrelated sandbox-blocked external accident check; rerun temp-dependent tests with a repository-scoped `--basetemp` and keep network tests isolated. The dedicated `.tmp_cron_wait_tests` directory was verified inside the worktree and removed.
- The same expected-wait rule is applied to `boatrace-race-detail-cron`: source `retry_wait` persists a data-status failure for retry/admin visibility but exits 0 to avoid a false Render crash; a genuinely blocked gate still exits 1.
- Production verification: bootstrap build `6273b81` completed and a manual run finished successfully in about four seconds with `official_ready=true`, `openapi_ready=false`, `status=waiting`, and no downstream gate. Race-detail build `f185fd9` completed; a concurrent verification run observed `skip state=running` and finished successfully, proving overlap suppression.
- The scheduled 07:30 detail generation reached motor cache 650/1080 before Render canceled it when the manual verification run overlapped. No destructive write or duplicate generation occurred. The stale-running guard delays automatic recovery until the first eligible 15-minute slot after 30 minutes (08:15 JST); verify the resulting full summary and 180-page integrity before declaring the daily detail cache complete.
- Public UI after deploy: `/healthz` HTTP 200; logged-in TOP reload about 1.01s with visible accident, escape, ace-motor `M`, and `!` tags; race detail first navigation 3.21s during the interrupted prewarm and reload 0.60s. Six boats rendered, M34 expanded with `aria-expanded=true`, and current plus historical motor rows were visible. Recheck the cold detail target after the 08:15 recovery.
