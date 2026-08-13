# 引継ぎ帳

最終更新日: 2026-08-09

## 0. この帳票の使い方

- 作業開始前に必ず読む
- 作業開始時に「今回の着手内容」「編集中ファイル」「実行予定ツール」を更新する
- 作業終了時に「完了」「次にやること」「未決事項」「実行中ツール / プロセス」を更新する
- 失敗や手戻りが出たら「失敗ログ」に具体例を書く

## 1. 今回の着手内容

- 全プロジェクト共通の運用ガード導入
- 共通スキル `project-ops-guard` の作成
- `AGENTS.md` / 引継ぎ帳 / チェックリストの整備

## 2. 完了したこと

- 2026-08-09: 共通運用ルールの導入準備
- 2026-08-09: `AGENTS.md` を追加
- 2026-08-09: `docs/handoff.md` を追加
- 2026-08-09: `docs/ops_checklist.md` を追加
- 2026-08-09: 他PJへ流用するテンプレ群を `docs/project_ops_templates/` に追加
- 2026-08-09: `scripts/install_project_ops_guard.py` を追加
- 2026-08-09: 共通スキル `project-ops-guard` を `C:\Users\tsuyo\.codex\skills\project-ops-guard` に配置

## 3. 次にやること

- 他の対象プロジェクトへ `scripts/install_project_ops_guard.py` を使って展開する
- 実際の運用で失敗ログを蓄積し、テンプレとスキルを改善する
- 必要なら各プロジェクト固有ルールを `AGENTS.md` に追記する

## 4. 決めたこと

- 作業前に必ず `AGENTS.md` とこの引継ぎ帳を読む
- 実行ツールは終了確認まで含めて記録する
- 削除前は一覧化、巻き戻し前は対象明示 + 確認を徹底する

## 5. 未決事項

- まだ決めていないことを書く

## 6. 編集中ファイル

- ファイルパス:
  - 担当:
  - 理由:
  - 競合回避メモ:

## 7. 実行予定ツール

- skill-creator quick_validate
- pytest
- 共通スキル配置コマンド

## 8. 実行中ツール / プロセス

- プロセス名:
  - 起動目的:
  - 停止条件:
  - 停止確認:

- 現在実行中: なし

## 9. 削除予定 / 削除実績

- 対象一覧:
- 理由:
- 影響範囲:
- 実行前確認:
- 実行結果:

## 10. 巻き戻し予定 / 巻き戻し実績

- 戻す対象:
- 戻す理由:
- 影響範囲:
- 実行前確認:
- 実行結果:

## 11. 失敗ログ

### 記入フォーマット

- 発生日:
- 作業:
- 失敗内容:
- 具体例:
- 原因:
- 再発防止策:
- スキル / 手順へ反映した内容:

### ログ

- 2026-08-09
  - 作業: 共通運用スキルの初期化
  - 失敗内容: skill 初期化時の UI 用 `short_description` が長すぎて生成エラー
  - 具体例: `short_description must be 25-64 characters`
  - 原因: UI 制約を満たす文字数確認前に初期化した
  - 再発防止策: `agents/openai.yaml` 制約を先に確認してから初期化する
  - スキル / 手順へ反映した内容: 共通スキル作成時は `openai_yaml.md` を先に確認する

## 12. ルール再掲

- 作業開始前にこの引継ぎ帳を読む
- 同じファイルの同時編集を避けるため、着手前に編集中ファイルを書く
- ツールは必ず閉じる
- 削除前は一覧化
- 巻き戻し前は対象明示 + 確認

---

## 2026-08-09 Live Check Result

- Checked:
  - https://boatrace-web.onrender.com/healthz
  - https://boatrace-web.onrender.com/races?date=2026-08-09
  - https://boatrace-web.onrender.com/race/20260809-05-07
- Result:
  - healthz returned HTTP 200
  - anonymous access to TOP and race detail redirected to /login
  - no 502 / server error text observed in headless browser output
- Files created:
  - eports/live_top_check_20260809.png
  - eports/live_race_check_20260809.png
- Failure log:
  - first PowerShell content probe failed because of command composition
  - prevention: use simpler PowerShell invocations or Playwright for page assertions
- Running tools/processes:
  - none left running

## 2026-08-10 Investigation

- Task:
  - Inspect live data for `market_signals:last-good:*`
  - Confirm today's `render_detail_tags_today` / `render_detail_pages_today`
  - Inspect today's accident snapshot presence and status
- Expected files:
  - `src/web/app.py`
  - `scripts/render_regular_scheduler.py`
  - `scripts/playwright_audit_pages.py`
  - `docs/handoff.md`
- Actual files changed:
  - `config.py`
  - `src/web/auth.py`
  - `.gitignore`
  - `scripts/playwright_audit_pages.py`
  - `tests/test_playwright_password_login.py`
  - `tests/test_supabase_auth_stripe_migration.py`
  - `docs/handoff.md`
- Skills:
  - `project-ops-guard`
  - `chrome:control-chrome`
- Running tools/processes:
  - Chrome session inspection for live admin/status pages
- Findings:
  - `admin/data-status?date=2026-08-10` shows `render_race_detail_all=success`, task detail `tags=144`, `pages=144`, `failed=0`, but actual race detail HTML cache is `12 / 144` with `132` missing.
  - TOP page embedded `initialMarketSignalsPayload.race_badges` is present for `61` races (`accident=52`, `ace_motor=16`), but rendered DOM badge counts are all `0`.
  - `public/roi?date=2026-08-10` shows `7` ROI rows live, so market-signal source data is not empty.
  - `member/accidents` shows snapshot period `2026-05-01 - 2026-08-09`, snapshot date `2026-08-09`, and large audit diff (`points diff 1046`, `total diff 1463`, non-zero points match `0.1%`).
- Open points:
  - `source_cache_key` for the live market-signal payload is not rendered in HTML, and extension-side direct API navigation is blocked by client rules.
  - Dedicated `render_detail_tags_today` / `render_detail_pages_today` task rows are not exposed in the current admin UI.
- Running tools/processes:
  - close Chrome inspection tabs before handoff

## 2026-08-10 Follow-up Fix

- Task:
  - Restore TOP badges rendering
  - Keep race detail fast by reusing stale/compat prewarmed HTML cache
  - Expose today's detail tag/page generation state in admin status
- Expected files:
  - `src/web/templates/index.html`
  - `src/web/app.py`
  - `tests/test_admin_data_status.py`
  - `tests/test_race_detail_page_prewarm.py`
  - `tests/test_l4_recent_odds_source.py`
  - `tests/test_source_regression.py`
- Skills:
  - `project-ops-guard`
  - `webapp-testing`
- Running tools/processes:
  - Playwright anonymous check against deployed login redirect
- Findings:
  - TOP page script had a duplicate `renderTodaysPicks` declaration. Embedded market-signal payload existed, but the syntax error could stop badge rendering before hydration.
  - `race_detail` could miss same-day prewarmed HTML after the fresh-cache TTL expired because it did not reuse stale/compat page cache variants.
  - admin data status did not show dedicated `render_detail_tags_today` / `render_detail_pages_today` visibility.
- Verification:
  - `py_compile` passed for:
    - `src/web/app.py`
    - `tests/test_admin_data_status.py`
    - `tests/test_race_detail_page_prewarm.py`
    - `tests/test_l4_recent_odds_source.py`
    - `tests/test_source_regression.py`
  - static source verification passed for:
    - `renderTodaysPicks = function()` presence
    - race detail compat cache helper usage
    - admin status entries for `race_detail_tags_today` and `race_detail_pages_today`
  - Playwright public check reached `/login?next=/races`, so authenticated live-page verification was blocked in this environment.
- Failure log:
  - Attempted `py -3 -m pytest ...`
  - Failure: Windows launcher session error from `py.exe`
  - Root cause: launcher unavailable in sandbox session
  - Prevention: use explicit runtime path instead of `py.exe`
  - Rule/checklist update: record exact Python runtime before running tests
  - Attempted bundled Python `-m pytest ...`
  - Failure: `No module named pytest`
  - Root cause: bundled runtime does not include project test dependencies
  - Prevention: either use the project venv or install `requirements-dev.txt` into an isolated env before full pytest

## 2026-08-10 Result FK Fix

- Task:
  - Fix `race_results_race_id_fkey` failures during `boatrace-regular-cron`
- Expected files:
  - `src/collectors/openapi.py`
  - `scripts/poll_results.py`
  - `tests/test_source_regression.py`
- Findings:
  - `poll_results.py` could receive Open API results for a race whose parent `races` row was missing.
  - `upsert_results()` wrote directly to `race_results` / `race_payouts` and relied on the parent row already existing.
- Changes:
  - Added `_ensure_race_shell(...)` in `openapi.py` so previews/results can create a minimal parent `races` row before child writes.
  - Added programs backfill in `poll_results.py` only when result payload contains missing parent races.
  - Added source regression checks for both protections.
  - Wrapped `upsert_results()` in per-race savepoints so one broken race payload does not block the rest of the day.
  - Added focused in-memory tests for result/preview parent-shell creation and broken-race isolation.
  - Reduced skipped-race warning logs to `race_id / stadium / race_no / err` instead of dumping the full payload.
- Verification:
  - `py_compile` passed for `src/collectors/openapi.py`, `scripts/poll_results.py`, `tests/test_source_regression.py`
  - static source verification passed for result-parent backfill flow
  - direct execution passed for:
    - `test_upsert_results_creates_parent_race_shell_before_child_rows`
    - `test_upsert_previews_creates_parent_race_shell_before_preview_rows`
    - `test_upsert_results_skips_only_broken_race_and_keeps_other_races`

## 2026-08-10 Cron Noise Fix

- Task:
  - Reduce cron-side warning noise unrelated to cron behavior
- Expected files:
  - `src/web/auth.py`
  - `src/web/app.py`
  - `tests/test_source_regression.py`
- Changes:
  - Made the open-redirect docstring in `auth.py` raw-string safe.
  - Limited default auth credential warnings in `app.py` to web runtime (`PORT` present) so cron imports do not emit misleading production auth warnings.
- Verification:
  - `py_compile` passed for `src/web/auth.py`, `src/web/app.py`, `tests/test_source_regression.py`

## 2026-08-10 Result Warning Threshold Alignment

- Task:
  - Reduce false-positive result warnings during live five-minute polling
- Expected files:
  - `scripts/check_post_run_integrity.py`
  - `scripts/poll_results.py`
  - `tests/test_source_regression.py`
  - `tests/test_race_detail_data_schedule.py`
- Findings:
  - `poll_results.py` treated races with no results 5 minutes after close as shell warnings.
  - `check_post_run_integrity.py` treated races closed 15 minutes ago as fully expected, which is still aggressive for late-night/result API lag.
- Changes:
  - Added `RESULT_SHELL_GRACE_MINUTES = 30` to `poll_results.py`.
  - Added `RESULT_CLOSE_GRACE_MINUTES = 30` to `check_post_run_integrity.py`.
  - Updated regression/source checks to pin both thresholds.
- Verification:
  - `py_compile` passed for `scripts/poll_results.py`, `scripts/check_post_run_integrity.py`, `tests/test_source_regression.py`, `tests/test_race_detail_data_schedule.py`
  - static verification passed for aligned 30-minute grace windows

## 2026-08-10 External Source Retry Trim

- Task:
  - Reduce wasted retries for venue-specific original exhibition hosts that hard-fail
- Expected files:
  - `src/collectors/_http.py`
  - `tests/test_source_regression.py`
- Changes:
  - Treat `connection refused` like DNS resolution failure in Layer 3 HTTP fetches and stop retrying within the same run.
- Verification:
  - `py_compile` passed for `src/collectors/_http.py`, `tests/test_source_regression.py`
  - static verification passed for the new guard

## 2026-08-10 Result Catch-up Investigation

- Task:
  - Reduce repeated `post-result` incompletes without switching Layer3 to unconditional full-day scraping
- Expected files:
  - `src/collectors/result_scraper.py`
  - `scripts/poll_results.py`
  - `tests/test_source_regression.py`
  - `tests/test_openapi_result_parent_shell.py`
- Skills:
  - `project-ops-guard`
  - `cron-watchdog`
- Findings:
  - `poll_results.py` runs Layer3 first, but the default path only targets L4/high-signal races.
  - When Open API stays late for non-L4 races, `check_post_run_integrity.py --stage post-result` can keep warning even though Layer3 ran successfully for only a tiny subset.
  - Recent production log example showed `Layer3 scrape: 2 races` while `post-result` still reported `27/144` closed races incomplete, which matches the narrow default Layer3 scope.
- Planned change:
  - Keep the current small Layer3 first pass.
  - After Open API upsert, detect races still missing full result rows beyond the grace window and run a second Layer3 repair only for those race ids.
  - Add focused regression coverage for the targeted repair flow.
- Changes:
  - `src/collectors/result_scraper.py`
    - added optional `race_ids` targeting so Layer3 can repair only selected races
    - preserved the lightweight L4/high-signal default pass
  - `scripts/poll_results.py`
    - added `_missing_closed_result_race_ids(...)`
    - added targeted `Layer3 repair` pass after Open API upsert
    - shell warning count now reuses the same missing-race detector
  - `tests/test_poll_results_targeted_repair.py`
    - added focused helper coverage for grace/completion filtering
  - `tests/test_source_regression.py`
    - pinned targeted repair wiring and explicit `race_ids` support
- Verification:
  - `py_compile` passed for:
    - `src/collectors/result_scraper.py`
    - `scripts/poll_results.py`
    - `tests/test_poll_results_targeted_repair.py`
    - `tests/test_source_regression.py`
  - direct execution passed:
    - `test_missing_closed_result_race_ids_obeys_grace_and_completion`
- Failure log:
  - Attempted direct file execution of `tests/test_poll_results_targeted_repair.py`
  - Failure: import chain failed first on `scripts` path, then `requests.exceptions`, then `bs4`
  - Root cause: bundled Python lacks the full app/test dependency set
  - Prevention: add repo root to `sys.path` and stub only the unused import chain for helper-only execution

## 2026-08-10 Top Page Browser Audit

- Task:
  - Inspect the deployed top/member page in a real browser
  - capture console errors, network timings, and determine whether `renderTodaysPicks` is exiting early, failing per-row, or blocked by missing DOM/data
- Expected files:
  - `src/web/templates/index.html`
  - `scripts/playwright_smoke_today_races.py`
  - `scripts/run_web.py`
  - `docs/handoff.md`
- Skills:
  - `project-ops-guard`
  - `webapp-testing`
- Running tools/processes:
  - local Flask test server on a temporary port for Playwright inspection
  - Playwright browser session against deployed and local pages

## 2026-08-10 Playwright Dedicated Password

- Task:
  - Add an optional Playwright-only password login without requiring a Supabase email.
  - Keep the existing member password unchanged and restrict the test session to read-only member views/APIs.
- Expected files:
  - `config.py`
  - `src/web/auth.py`
  - `src/web/membership.py`
  - `src/web/billing.py`
  - `src/web/app.py`
  - `tests/test_playwright_password_login.py`
  - `.gitignore`
  - `docs/handoff.md`
- Conflict avoidance:
  - `src/web/auth.py` and `src/web/app.py` already contain unrelated active changes; preserve those hunks and add only isolated authentication guards.
- Skills:
  - `project-ops-guard`
  - `security-best-practices`
  - `webapp-testing`
- Planned verification:
  - focused pytest authentication tests
  - local Playwright login and read-only authorization check
- Running tools/processes:
  - none; final Flask server and headless Chromium audit were stopped
- Deletion targets (task-generated only, confirmed safe):
  - `playwright/.auth/local-test-state.json` (temporary authenticated session; must not remain)
  - `reports/playwright_password_local/` (superseded intermediate audit)
  - `reports/playwright_password_state_create/` (superseded intermediate audit)
  - `reports/playwright_password_state_reuse/` (superseded intermediate audit)
- Deletion impact/recovery:
  - No application or user data; the temporary audit files can be regenerated locally.
- Failure log:
  - Attempted combined `rg` role search with an escaped parenthesis expression.
  - Failure: `regex parse error: unclosed group`.
  - Root cause: PowerShell/regex escaping made the grouped expression invalid.
  - Prevention: use separate fixed-string `rg -F` searches for code tokens.
  - Checklist update: prefer fixed-string searches for literal Python expressions.
  - Attempted the webapp-testing `scripts/with_server.py --help` path.
  - Failure: helper script is not installed; the skill directory contains only `SKILL.md`.
  - Root cause: this local skill package omits the documented helper scripts.
  - Prevention: use the repository's existing `scripts/run_web.py` and explicitly track/stop its process.
  - Attempted the first combined authentication patch.
  - Failure: `apply_patch verification failed` at a line containing a previously mojibaked comment.
  - Root cause: the patch context included unstable non-ASCII comment text.
  - Prevention: anchor small patches on adjacent ASCII-only code lines.
  - Checklist update: keep patch context minimal in files with encoding history.
  - Ran the focused authentication suite after implementation.
  - Failure: `test_legacy_password_login_is_not_admin` expected the old unconditional assignment string.
  - Root cause: the source-regression assertion did not account for the new paid-member/test-viewer conditional.
  - Prevention: assert both explicit conditional branches while retaining the no-admin assertion.
  - Ran the password-mode Playwright audit against the local login page.
  - Failure: strict-mode selector matched both the header date-submit button and login-submit button.
  - Root cause: `button[type="submit"]` was not scoped to the login form.
  - Prevention: use `.login-form button[type="submit"]` for production-form login.
  - Checklist update: scope Playwright selectors to the owning form when shared controls exist.
  - Re-ran the password-mode Playwright audit with an empty local race dataset.
  - Failure: `.stadium-grid` did not exist and the audit timed out after 30 seconds.
  - Root cause: the audit treated a populated race grid as mandatory instead of accepting the valid empty-data page.
  - Prevention: wait for `body`, then report `stadium_grid_visible` separately.
  - Checklist update: distinguish page-load assertions from optional data-content assertions.
  - Reviewed the saved-storage-state branch before production use.
  - Failure: password mode with an existing state would fall through to `/test/login-as`.
  - Root cause: the password branch condition only handled the no-state case.
  - Prevention: keep all password-mode paths in one branch and explicitly validate saved-session expiry.
  - Checklist update: test both initial authentication and persisted-session reuse branches.
- Completed:
  - Added optional `BOATRACE_PLAYWRIGHT_PASSWORD`; empty or unsafe values disable the dedicated login.
  - Dedicated login creates `test_viewer / playwright_password` session without Supabase email.
  - Test viewer can use member GET pages/APIs but all non-safe HTTP methods are rejected with 403.
  - Existing member password still creates `paid_member / legacy_password` session.
  - Playwright audit supports password login and ignored `storageState` reuse without printing secrets.
  - `--prompt-password` supports hidden first-run input so the password does not enter shell history or chat.
  - Dedicated sessions carry an HMAC password version and expire automatically when the Render secret is changed or removed.
  - Temporary authenticated state and superseded audit outputs were removed; final audit remains in `reports/playwright_password_final/`.
- Verification:
  - focused pytest: `44 passed`
  - `py_compile`: passed
  - Playwright login: `POST /login -> 302`, member page `200`
  - Playwright authorization: admin GET `403`, cache-clear POST `403`
  - final HMAC-session Playwright rerun: passed
  - saved authentication creation and no-password reuse: passed
  - secret scan: local test password not present in repository files
  - authentication state file: deleted; `playwright/.auth/` confirmed ignored
- Next action:
  - Set a unique 16+ character `BOATRACE_PLAYWRIGHT_PASSWORD` secret in Render when deploying this change.

## 2026-08-10 Playwright Password Deployment

- Task:
  - Publish the dedicated Playwright password implementation after the Render secret was configured.
  - Verify Render deployment health and the production login surface without exposing the secret.
- Commit scope:
  - `.gitignore`
  - `config.py`
  - `scripts/playwright_audit_pages.py`
  - `src/web/auth.py`
  - `tests/test_playwright_password_login.py`
  - `tests/test_supabase_auth_stripe_migration.py`
- Conflict avoidance:
  - Do not stage unrelated cron, collector, TOP, race-detail, report, or project-ops files in the mixed worktree.
- Skills:
  - `project-ops-guard`
  - `webapp-testing`
  - inspected `github:yeet`; PR flow is not used because this repository's established deployment path pushes the current branch directly to `origin/main`.
- Verification plan:
  - inspect staged diff
  - rerun focused tests
  - push one scoped commit
  - poll production health and deployment commit
  - verify public production login page with Playwright; authenticated verification requires a local state or hidden password prompt
- Running tools/processes:
  - Render Events Chrome tab kept for handoff; no local process running
- Failure log:
  - Initial scoped `git add` failed with `index.lock: Permission denied`.
  - Root cause: this checkout is a linked worktree whose Git index lives outside the writable workspace sandbox.
  - Prevention: request scoped escalation for Git index mutations while keeping explicit file paths.
  - Checklist update: detect linked-worktree Git dir before staging in restricted sessions.
- Completed:
  - focused authentication suite: `44 passed`
  - committed scoped files as `0955a2b Add read-only Playwright login`
  - pushed `0955a2b` to `origin/main`
  - user rotated the Render Playwright password after the browser-inspection exposure
  - Render deployed `0955a2b` and reports it as live
  - production `/healthz`: HTTP 200 in 213 ms
  - production `/login`: HTTP 200 in 358 ms; login form present
  - production browser check: login page rendered; no application JavaScript errors
  - production dedicated login: HTTP 302 to `/`, member page HTTP 200
  - production test-viewer authorization: admin GET HTTP 403, cache-clear POST HTTP 403
- Deployment state:
  - Render Events shows `0955a2b` live as of 2026-08-10 23:16 JST.
  - No further deployment action is pending for this change.
- Security note:
  - The Render environment page exposed the newly entered Playwright secret to browser automation DOM inspection.
  - The user confirmed that value was rotated before authenticated production testing.
  - Do not copy the value into chat, source files, logs, or shell history.
  - The production test accessed the rotated value only through `os.environ` inside the Render instance and printed no secret.

## 2026-08-10 TOP P1 Performance Investigation

- Task:
  - Measure the private test-viewer TOP page three times cold and three times warm.
  - Record console errors, slow network requests, server processing time, and `renderTodaysPicks` results.
  - Identify the frontend/API/DB/external bottleneck, apply the smallest safe fix, remeasure, and run focused regression tests.
- Expected files:
  - `scripts/profile_top_p1.py`
  - `src/web/templates/index.html`
  - `src/web/app.py`
  - focused tests under `tests/`
  - `docs/handoff.md`
- Conflict avoidance:
  - `src/web/app.py`, `src/web/templates/index.html`, and several tests already contain unrelated worktree changes; preserve existing hunks and edit only the measured bottleneck.
- Skills:
  - `project-ops-guard`
  - `webapp-testing`
- Planned verification:
  - authenticated Playwright cold/warm runs, three samples each
  - console/network/server-timing/`renderTodaysPicks` evidence capture
  - focused pytest and post-fix three-by-three remeasurement
- Running tools/processes:
  - none at task start
- Failure log:
  - Attempted the first three-pair Playwright profile in a fresh unauthenticated context.
  - Failure: `/races` returned HTTP 302 and the browser measured `/login`, so race count was zero.
  - Root cause: the deployed service currently requires a member session for the TOP route.
  - Prevention: assert the final URL and nonzero race-card count before accepting a performance sample; reuse a logged-in private browser session without reading its password.
  - Checklist update: authenticated performance probes must fail fast on login redirects.
  - Attempted a SQLite count probe with inline Python under PowerShell.
  - Failure: PowerShell quoting split the SQL and produced parser/SyntaxError output twice.
  - Root cause: nested quoting in `python -c` was not stable in this shell.
  - Prevention: use an existing script or a file-based helper for nontrivial Python; the corrected probe confirmed the local DB contains zero races and is unsuitable for real-data timing.
  - Ran `test_today_races_page`, `test_prewarm_strategy_pages`, and the broader source-regression file together.
  - Failure: 6 of 70 tests failed only in pre-existing source-regression expectations (morning badge text, reference filtering, monthly ROI window, old self-heal behavior, and an older race-detail cache version); the 19 TOP tests and 15 prewarm tests passed.
  - Root cause: the mixed worktree already contains behavior changes not reflected in those broad source-string assertions.
  - Prevention: do not rewrite unrelated expectations; run focused TOP/ROI tests for this scoped change and report the broader existing failures separately.

### Rin performance ownership

- Owner: secretary Rin.
- P1 target: authenticated production TOP initial display and reload median must each be `<= 1500 ms` across three runs.
- P2 target: authenticated production race-detail initial display and reload median must each be `<= 1500 ms` across three runs.
- Completion guard: payload reduction or server-only timing is supporting evidence, not completion. Both browser medians must pass, required content must render, and console/page errors must be zero.
- Iteration loop: measure -> classify frontend/API/DB/external -> apply the smallest scoped fix -> run related tests -> deploy -> repeat the same measurements -> run regression checks.
- Safety: preserve the current design, do not change or delete DB data/schema without an explicit request, and never expose test credentials or response bodies containing secrets.

### P1 evidence and current status

- Before-fix authenticated browser samples:
  - initial: `4654 / 3629 / 3650 ms`, median `3650 ms`
  - reload: `2410 / 1842 / 1932 ms`, median `1932 ms`
- Before-fix response/DOM:
  - server response: `220584 bytes`
  - rendered HTML: `202402 chars`
  - inline JavaScript: `105729 chars`
- Server-side authenticated `/races` processing was `19.8-25.9 ms` across six requests, so API/DB/server rendering was not the primary bottleneck.
- Minimal fix:
  - TOP now uses a lightweight runtime and does not load ROI-only `renderTodaysPicks` or `loadMarketSignals` logic.
  - Required accident, ace-motor, escape, entry-change, next-race, and closed-race rendering remains available.
  - Commit `0ab84df` reduced the response to `135128 bytes` (`-38.7%`) and inline JavaScript to `23990 chars` (`-77.3%`).
  - Commit `ca56cbe` adds production runtime diagnostics without changing visible behavior.
- Post-fix stable browser samples:
  - initial: `7829 / 3698 / 3614 ms`, stable median `3698 ms` (first sample is a browser/deploy outlier)
  - reload: `2079 / 2181 / 2187 ms`, median `2181 ms`
- Required tag verification: `144` races, `12` stadiums, `52` accident badges, and `16` ace-motor badges rendered. Escape and entry-change counts were zero in the current payload.
- P1 status: **not achieved**. The server remains approximately `21-24 ms`, while browser wall time is still above `1500 ms`; remaining work is frontend parse/layout/paint measurement and DOM reduction that preserves all required data.
- P2 status: **not started in this measurement cycle**. Establish the same three-initial/three-reload baseline on a populated current race-detail page before editing.

## 2026-08-11 Rin P1/P2 Pause Handoff

- Pause reason:
  - A separate clone process is expected to start in about 20 minutes and may increase server load.
  - Stop further production deployment, profiling, and load-generating requests until the next work session.
- Production state:
  - `3979ce0 Add browser navigation diagnostics` deployed live.
  - `10c535c Defer offscreen top page rendering` deployed live.
  - `b9254eb Invalidate stale race detail HTML` deployed live and is the current production commit.
- P1 TOP result:
  - Lightweight mode verified: `renderTodaysPicks` calls `0`, market-signals requests `0`, runtime errors `0`.
  - Required data remained present: `144` races and `12` stadiums; accident and ace-motor DOM classes were present.
  - Offscreen stadium layout now uses `content-visibility: auto` while retaining the complete DOM and links.
  - Warm post-fix browser samples: `1769 / 1771 / 7801 ms`; median `1771 ms`.
  - Warm post-fix render work after HTML receipt is approximately `50-60 ms`; the remaining delay is TTFB (`1710 / 1717 / 7741 ms`).
  - P1 end-to-end target `<=1500 ms` is not achieved. Application-side render work is below the target; Render/network TTFB is now the limiting layer.
- P2 race-detail result:
  - Production page `20260810-03-01` rendered `6` rows and `6` motor buttons without visible errors.
  - Authenticated server requests were `18.3-20.3 ms` across six runs; response size was `48677 bytes` before the diagnostics build.
  - Browser samples after cache repair: `3721 / 7520 / 1817 ms`; normal render work after receipt was approximately `65 ms`, while TTFB was `1801 / 7450 / 1751 ms`.
  - Motor-detail click regression passed: inspector became visible, loading completed, and populated six-boat motor-position content appeared.
  - P2 end-to-end target `<=1500 ms` is not achieved. Application-side server/render work is below the target; Render/network TTFB is the limiting layer.
- Bug fixed:
  - Race-detail cached HTML retained the old `race_detail.js` URL after deployment, so new JavaScript could remain inactive.
  - `RACE_DETAIL_PAGE_CACHE_VERSION` was bumped from `v12` to `v13`; focused tests passed and the repaired page loaded the new script.
  - Prevention: every race-detail template or JavaScript behavior change must verify whether the HTML cache version also needs a bump.
- Verification:
  - focused TOP/race-detail suite: `20 passed` before offscreen-layout deploy.
  - cache-version regression suite: `31 passed`.
  - `node --check src/web/static/race_detail.js`: passed.
  - Render health checks passed for all three deployments.
- Failure log additions:
  - A Chrome navigation exceeded the 10-second CDP deadline during a Render latency spike; retry recovered the loaded page. Do not classify this as frontend failure without checking Navigation Timing.
  - The first detail timing read occurred before the delayed page reached `load`, returning an empty diagnostic object. Wait for `document.readyState === "complete"` and a populated dataset before accepting a sample.
  - Initial `git push origin main` targeted an older local `main` ref and was rejected. Use `git push origin HEAD:main` from the active Codex branch.
  - A PowerShell `rg` pattern and a later latency-probe pipeline had quoting/parser errors. Prefer simple single-quoted `rg` patterns and file-based or separately assigned PowerShell output for complex probes.
- Tomorrow restart order:
  - Confirm no clone/heavy cron is running and check `/healthz` once.
  - Confirm Render still reports `b9254eb` live; do not redeploy unless production differs.
  - Measure local-to-Render `healthz` and static-resource latency six times without authentication to validate the TTFB hypothesis.
  - Repeat TOP and detail three-run Navigation Timing with the same URLs and report median plus maximum.
  - If TTFB remains above `1500 ms` while internal processing stays below `25 ms`, treat the remaining target as an infrastructure/region/CDN decision rather than adding risky application complexity.
- Running tools/processes:
  - No local server, profiler, or shell command remains running.
  - Keep the authenticated Render tab for handoff; close the agent-owned performance tab when ending the browser session.

## 2026-08-11 Admin data-status morning false-alarm check

- Task:
  - Inspect whether `admin/data-status?date=2026-08-11` is showing real cron failures or partial morning generation as false `error`.
- Expected files:
  - `src/web/app.py`
  - `tests/test_admin_data_status.py`
  - `docs/handoff.md`
- Skills:
  - `project-ops-guard`
  - `cron-watchdog`
- Findings so far:
  - Current status logic marks partial cache counts as `error` before it considers `task_run.status == running`.
  - That means the 07:00 morning race-detail build can appear red while it is still legitimately building pages and per-boat caches.
- Active edit scope:
  - limit changes to admin status classification for in-progress partial generation and add focused regression coverage.
- Changes:
  - added `_mark_admin_item_running_partial_warning(...)` in `src/web/app.py`
  - race detail / detail tags / detail pages / motor history / racer detail now downgrade to `warning` when the owning morning task is still `running` and there is no explicit `system_status=error`
  - added focused regression case in `tests/test_admin_data_status.py` for partial counts during the morning build
- Commit / deploy:
  - committed as `b2b6fa6 Downgrade in-progress morning cache alerts`
  - pushed to `origin/main`
  - production `https://boatrace-web.onrender.com/healthz` returned HTTP `200` after push
- Automation:
  - recreated `リン 9時に再開` as a real heartbeat automation
  - automation id: `9`
- Verification:
  - `py_compile` passed for:
    - `src/web/app.py`
    - `tests/test_admin_data_status.py`
  - source verification passed for:
    - `_mark_admin_item_running_partial_warning`
    - running partial warning hint text
    - new test case name
- Remaining:
  - full pytest not run in this sandbox because bundled Python lacks `pytest` and app deps like `flask`
- Deletion targets (task-generated only, safe):
  - `tmp_admin_false_alarm_stage.patch`
  - `tmp_build_stage_files.js`
  - `tmp_stage_app.py`
  - `tmp_stage_test_admin_data_status.py`
- Deletion impact / recovery:
  - all are temporary staging helpers and can be regenerated from this handoff section if needed
- Failure log:
  - `git status --short` failed with `detected dubious ownership`.
  - Root cause: sandbox user differs from repository owner in this checkout.
  - Prevention: avoid broad git operations during diagnosis; if staging is needed later, use the already-established scoped escalation path.
  - attempted direct invocation of the new test via bundled Python
  - failure: bundled runtime could not import `flask`
  - root cause: this sandbox Python does not include repository application dependencies
  - prevention: use the project runtime/venv or CI environment for full test execution before deploy
  - attempted `git apply --cached` with a hand-built patch file
  - failure: patch was corrupt because hunk headers did not match the exact added-line counts
  - root cause: mixed context and mojibake made the manual patch inaccurate
  - prevention: synthesize exact staged blobs from `HEAD` and update the index by blob hash when the worktree is dirty

## 2026-08-11 TOP lightweight badge snapshot self-repair

- Task:
  - Keep Rin's 09:00 restart automation active and continue the live TOP-page investigation.
  - Fix the production TOP page case where accident / ace / warning badges disappear because a lightweight snapshot contains an empty badge payload.
- Expected files:
  - `src/web/app.py`
  - `tests/test_today_races_page.py`
  - `docs/handoff.md`
- Conflict avoidance:
  - `src/web/app.py` and `tests/test_today_races_page.py` already contain unrelated worktree changes.
  - Do not stage the full files directly; synthesize `HEAD`-based staged blobs that include only this snapshot-repair fix.
- Skills:
  - `project-ops-guard`
  - `control-chrome`
- Running tools/processes:
  - Chrome session kept alive for live production verification.
  - Open tabs:
    - admin data-status
    - accidents page
    - TOP page
  - helper script prepared:
    - `tmp_build_stage_top_badges.js`
- Live findings:
  - production admin page improved to `正常 2 / 注意 3 / 異常 0`
  - `モーター履歴` and `選手情報` are normal in the live admin page
  - `事故情報` remains a meaningful warning because the live accidents page still shows a large snapshot diff
  - TOP page has no frontend runtime error, but its lightweight embedded payload is empty:
    - `renderTodaysPicks.calls = 0`
    - `marketSignalsRequests = 0`
    - runtime errors = `0`
    - embedded payload contains `race_badges = {}` and `accident_watch = {}`
  - root cause: the `/races` snapshot path reuses an existing lightweight snapshot even when that snapshot's badge payload is empty, so no accident / ace / warning badges are rendered.
- Active edit scope:
  - add a lightweight-payload badge-presence guard
  - repair empty badge payloads from race IDs already present in the cached stadium groups
  - persist the repaired lightweight snapshot
  - add focused regression coverage for the empty-snapshot repair path
- Verification:
  - source checks confirm the new helper and focused test exist in the worktree
  - `py_compile` already passed for:
    - `src/web/app.py`
    - `tests/test_today_races_page.py`
- Next actions:
  - execute the `HEAD`-based staging helper
  - stage only the scoped blobs for `src/web/app.py` and `tests/test_today_races_page.py`
  - commit / push / deploy
  - reload the live TOP page in Chrome and confirm the embedded payload and badge DOM are repaired
- Failure log:
  - the live TOP page initially looked like a client-render failure because no accident or ace badges were visible
  - deeper Chrome inspection showed no app console error and no missing DOM container; the embedded lightweight payload itself was empty
  - prevention: production TOP investigations must inspect both the DOM and the embedded lightweight payload before changing client JavaScript

### 2026-08-11 continuation: deployed fix and verified badge recovery

- Root cause confirmed:
  - production Render was still serving commit `f1ac2e2` even after newer fixes were pushed
  - `market_signals:last-good:2026-08-11` already contained accident / ace badge data
  - the public TOP page stayed empty because the new TOP repair code had not been deployed yet
- Additional commits:
  - `22011d9 Repair lightweight top badge snapshot`
  - `443017b Hydrate top badges for cache-only signals`
  - `63e1dcc Refresh top snapshot after signal recompute`
- Render deployment:
  - `2026-08-11 17:01 JST` manual deploy started from Render dashboard
  - `2026-08-11 17:02 JST` Render reported `Deploy live for dade455`
  - `dade455` is newer than the fixes above and includes them in `origin/main` history
- Verified data-state after next cron slot:
  - `market_signals:last-good:2026-08-11` updated_at advanced to the same 17:06 bucket as TOP
  - `top_page_snapshot:v3:v27:2026-08-11` updated_at also advanced to the same 17:06 bucket
  - `render_signal_refresh_17_0_exhibition` succeeded at `2026-08-11T17:06:44`
- Browser verification:
  - fresh production TOP tab after deploy and cron refresh showed:
    - `hasAccidentBadge = true`
    - `hasAceBadge = true`
    - embedded `const payload = ...` contained many `race_badges`
    - visible badge text included repeated `事故` and `逃げ`
    - runtime errors remained `0`
- Important note:
  - direct `Invoke-WebRequest` from this session continued to fail intermittently even while Render internal `healthz` returned `200`
  - Render dashboard app logs were the reliable source for deploy-health confirmation in this session
