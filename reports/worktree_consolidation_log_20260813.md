# Worktree Consolidation Log (2026-08-13)

## Scope and safeguards

- Authoritative plan: `reports/worktree_consolidation_plan_20260813.md` (read directly as UTF-8).
- Scope: safe and executable portions of Phase 0 through Phase 2 only.
- Prohibited and not performed: push/merge to `origin/main`, Render/Supabase changes or deployment, scheduled-task changes or execution, deletion of any file/worktree/branch, `git reset --hard`, force push, and execution of `scripts/install_all_tasks.ps1` or `scripts/startup_catchup.py`.
- Rescue policy: preserve only source, configuration, tests, and reports; exclude temporary files, caches, screenshots, and large images.

## Preflight

- Read `AGENTS.md`, `CLAUDE.md`, `docs/handoff.md`, `docs/ops_checklist.md`, and the authoritative consolidation plan.
- `docs/secretary_status.json` was not present in the target repository; this was recorded as an existing documentation gap and was not created because it is outside the consolidation plan.
- Confirmed the main worktree is `main` at `dade455`, 55 commits behind `origin/main`, with the untracked paths listed in the plan plus the plan itself.
- Confirmed all nine worktrees in the plan are still registered. No deletion or mutation was performed during preflight.

## Commands executed before Phase 0

```powershell
Get-Content -LiteralPath 'C:\boat_project\boatrace-analysis\reports\worktree_consolidation_plan_20260813.md' -Encoding UTF8
Get-Content -LiteralPath 'C:\boat_project\boatrace-analysis\AGENTS.md' -Encoding UTF8
Get-Content -LiteralPath 'C:\boat_project\boatrace-analysis\CLAUDE.md' -Encoding UTF8
Get-Content -LiteralPath 'C:\boat_project\boatrace-analysis\docs\handoff.md' -Encoding UTF8
Get-Content -LiteralPath 'C:\boat_project\boatrace-analysis\docs\ops_checklist.md' -Encoding UTF8
git -C 'C:\boat_project\boatrace-analysis' status --short --branch
git -C 'C:\boat_project\boatrace-analysis' worktree list --porcelain
```

## Phase 0

- Attempted the required full-ref bundle first:

```powershell
git -C 'C:\boat_project\boatrace-analysis' bundle create 'C:\boat_project\backup-boatrace-all-20260813.bundle' --all
```

- Result: failed before creation with `Permission denied` for `C:/boat_project/backup-boatrace-all-20260813.bundle.lock` because the execution sandbox cannot write to the requested backup directory.
- The parent operator subsequently obtained approval, created the required outputs, and reported XML validation success. This run independently verified the files exist and verified the bundle:
  - `C:\boat_project\backup-boatrace-all-20260813.bundle`
  - `C:\boat_project\backup_task_BoatracePcNightlyPrepare.xml`
  - `C:\boat_project\backup_task_BoatraceLocalSupabaseSync.xml`
- `git bundle verify C:\boat_project\backup-boatrace-all-20260813.bundle` succeeded and reported complete history with 21 refs.
- Backup sizes at verification: bundle 7,336,360 bytes; `BoatracePcNightlyPrepare` XML 3,104 bytes; `BoatraceLocalSupabaseSync` XML 3,200 bytes.
- No scheduled task was changed or run.
- A combined PowerShell existence-check command had a parser error (`EmptyPipeElement`). It performed no file operation. Prevention: the three `Test-Path -LiteralPath` checks were rerun independently and succeeded.
- Phase 0 status: **complete**.

## Phase 1

- Phase 1 status: **complete**. Existing bytes were preserved without rewriting user changes.
- Read-only classification used:

```powershell
git -c safe.directory='<worktree-path>' -C '<worktree-path>' status --porcelain=v1 -uall
```

- `safe.directory` was applied per command only for worktrees rejected by the sandbox ownership check. No global Git configuration was changed.

### Worktree #6: `boatrace-cron-audit-worktree`

- Rescue branch: `rescue/boatrace-cron-audit-worktree-wip-20260813`.
- Commit: `42ed768` (`22 files changed, 1828 insertions, 7 deletions`).
- Rescue all 22 listed paths: 5 modified configuration/source/test files (`render.yaml`, `scripts/ensure_alert_tables.py`, `src/db/schema.sql`, `src/notifications/subscribers.py`, `tests/test_race_detail_data_schedule.py`), 5 Markdown reports, 10 audit/backtest/report scripts, and `tests/test_alert_subscribers_schema.py`.
- Exclusions: none shown by `git status -uall`.

### Worktree #7: `boatrace-deploy-worktree`

- Rescue branch: `rescue/boatrace-deploy-worktree-wip-20260813`.
- Commit: `800ba71` (`77 files changed, 1942 insertions, 149 deletions`).
- Rescue all listed modified/untracked paths: 53 modified source/script/test files plus the Markdown RLS audit, RLS planning/profiling scripts, SQL inventory, Supabase migration, and 18 new tests.
- `scripts/startup_catchup.py` was included only as an existing modified source file; it was not executed.
- Exclusions: none shown by `git status -uall`; no screenshots, caches, or temporary paths were listed.

### Worktree #8: `boatrace-main-deploy`

- Rescue branch: `rescue/boatrace-main-deploy-wip-20260813`.
- Commit: `8c358ca` (`28 files changed, 2072 insertions, 246 deletions`).
- Rescue modified `README.md`, application/source scripts, collectors, `src/web/app.py`, and tests; also rescue `AGENTS.md`, project-ops documentation/templates, `scripts/install_project_ops_guard.py`, `tests/test_install_project_ops_guard.py`, `tests/test_openapi_result_parent_shell.py`, `tests/test_poll_results_targeted_repair.py`, and `reports/kraw_unmatched_accident_rows.csv`.
- Exclude generated screenshots/images: `playwright_today_races_20260809.png`, `playwright_today_races_20260809_final.png`, `reports/live_race_check_20260809.png`, `reports/live_top_check_20260809.png`, `reports/playwright_password_final/member_today_races.png`, `reports/playwright_password_final/top_races.png`, `reports/playwright_today_races_local_20260810.png`, `reports/todays_picks_probe_local.png`, and `reports/todays_picks_probe_wrapper.png`.
- Exclude generated probe/report JSON: `reports/playwright_password_final/report.json`, `reports/todays_picks_probe_local.json`, `reports/todays_picks_probe_wrapper.json`, and `reports/top_p1_before.json`.
- Exclude temporary helpers/staging files: `scripts/tmp_member_route_probe.py`, `scripts/tmp_playwright_server.py`, `scripts/tmp_todays_picks_browser_probe.py`, `tmp_build_stage_top_badges.js`, `tmp_stage_top_badges_app.py`, and `tmp_stage_top_badges_test_today_races_page.py`.

### Worktree #9: `boatrace-main-deploy-pushsync`

- No dirty source rescue is needed. All untracked entries are under `.pytest_tmp_*` or `.tmp_night_version_audit` and are excluded as test-generated temporary data.
- Existing commit `63323df` is assessed separately in Phase 2.

### Phase 1 verification

- Worktrees #6 and #7 are clean after rescue commits.
- Worktree #8 contains only the excluded untracked paths listed above.
- No excluded path was staged or committed. No file, branch, or worktree was deleted.

## Phase 2

- Read-only commands executed:

```powershell
git -C 'C:\boat_project\boatrace-analysis' log origin/main --oneline -30
git -C 'C:\boat_project\boatrace-analysis' cherry -v origin/main deploy-main
git -C 'C:\boat_project\boatrace-analysis' cherry -v origin/main codex/main-motor-fix-20260809
git -C 'C:\boat_project\boatrace-analysis' cherry -v origin/main codex/pushsync-top-badges
git -C 'C:\boat_project\boatrace-analysis' show --stat --oneline --no-renames bdf9cef 87f06cf e94ebe8 5359c35 0269e82 f11a83d 300e028 63323df
git -C 'C:\boat_project\boatrace-analysis' grep ... origin/main -- <relevant files>
```

### Findings

- `bdf9cef` Guard motor detail payload mismatch: patch-equivalent content is already in `origin/main` (`da6e825`); do not rescue again.
- `87f06cf` Use delegated motor-detail click handling: patch-equivalent content is already in `origin/main`; do not rescue again.
- `0269e82` Fix legacy race id compatibility for motor detail: patch-equivalent content is already in `origin/main` (`7dbd75d`); do not rescue again.
- `300e028` Repair lightweight top badge snapshot: patch-equivalent content is already in `origin/main`; do not rescue again.
- `5359c35` Fix motor detail fallback for cached race pages: `git cherry` did not match because of branch/context differences, but `origin/main` contains the same fallback via `5ed0183`; current origin has `ensureInspectorShell()` and a later cache version (`v15`). Do not rescue again.
- `e94ebe8` Disable browser caching for member race pages: `origin/main` already contains the same `no-store, no-cache, must-revalidate, private` behavior and matching tests. Do not rescue again.
- `f11a83d` Track odds fetch status and audit readiness: no `odds_fetch_status`, `fetch_html_detailed`, or equivalent readiness implementation was found in `origin/main`. This is a substantive 12-file/985-line candidate for preservation and later review.
- `63323df` Prevent recurring login DB exhaustion: this builds on `origin/main` commit `c32c6b1` by bypassing the shared pool for task-triggered processes and adds cache/schema checks. It is not patch-equivalent to `origin/main` and remains a candidate for preservation and later review; it may have non-trivial interaction with the newer pool-pressure fixes and must not be merged automatically.
- Created `rescue/unmerged-commits-20260813` from `origin/main` in worktree #9.
- Cherry-picked `f11a83d` without conflict as `d6fb8d5` (`12 files changed, 985 insertions, 31 deletions`).
- Cherry-picked `63323df` without conflict as `1edacff` (`8 files changed, 163 insertions, 33 deletions`).
- Branch tip: `1edacff`; exactly two commits ahead of `origin/main`.
- Worktree #9 retains only excluded `.pytest_tmp_*` and `.tmp_night_version_audit` test-generated data.
- No merge or push was performed.

## Approval gates and unresolved items

- Phase 0 through Phase 2 are complete.
- Phase 3 main update, Phase 4 scheduled-task reassignment/run, and Phase 5 deletion remain out of scope and require separate explicit user approval.
- `docs/secretary_status.json` remains absent in the target repository.

## Final verification for this scope

- Main worktree remains on `main` at `dade455`, 55 commits behind `origin/main`; no main merge/pull/push occurred.
- Rescue tips: `42ed768`, `800ba71`, `8c358ca`, and `1edacff`.
- Verified all five created commit objects (`42ed768`, `800ba71`, `8c358ca`, `d6fb8d5`, `1edacff`) with independently quoted `git rev-parse --verify '<id>^{commit}'` commands.
- An initial combined `git cat-file -e <id>^{commit}` PowerShell command failed because unquoted braces were parsed as ScriptBlocks; it changed nothing. Prevention: quote revision expressions and verify each object independently.
- All nine worktrees remain registered. No deletion, reset, force push, deployment, Supabase/Render change, scheduled-task change, or scheduled-task run occurred.

## Phase 3: canonical repository update

- Preserved the canonical worktree's audit files and consolidation records on `rescue/main-audit-assets-20260813` as commit `db2f418` before updating `main`.
- Fast-forwarded `C:\boat_project\boatrace-analysis` from `dade455` to `cb442f4` with `git pull --ff-only origin main`.
- Verified local `main` and `origin/main` both resolve to `cb442f4`; ahead/behind is `0/0`.
- Ran the complete test suite: `550 collected`, `535 passed`, `15 failed` in 13.99 seconds.
- The 15 failures match the existing current baseline and cover stale source assertions, authorization fixtures, legacy motor-history status expectations, and badge-label fixtures. No new failure attributable to the fast-forward was found.
- No push, deployment, scheduled-task change/run, worktree deletion, branch deletion, reset, or force push was performed.

## Phase 4 proposal (not executed)

- Current task: `BoatracePcNightlyPrepare`, state `Ready`, next run `2026-08-14 01:00 JST`.
- Current executable: `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy\scripts\run_pc_nightly_prepare.bat`.
- Proposed executable: `C:\boat_project\boatrace-analysis\scripts\run_pc_nightly_prepare.bat`.
- The proposed batch file resolves its repository root from its own location, honors `.pc_schedule_paused`, writes `logs\pc_nightly_prepare.log`, and uses the canonical `.venv` Python executable.
- Last task run was `2026-08-13 01:00:01 JST` with result code `1` (failure). The task definition and execution were not changed during this phase.
- Await explicit user approval before changing the task action or running it manually.
