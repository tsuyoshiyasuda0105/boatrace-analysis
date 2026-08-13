# Worktree Consolidation Log (2026-08-13)

## Prior record

- The Phase 0-4 log and authoritative plan are preserved on `rescue/main-audit-assets-20260813` at `80e72a2` (prior record also exists at `db2f418`).
- After Phase 3, both files were absent from the canonical main worktree. This file resumes the operational record for the explicitly approved Phase 5 execution.

## Phase 5 preflight

- User explicitly approved execution of the Phase 5 deletion list on 2026-08-13.
- Verified `C:\boat_project\backup-boatrace-all-20260813.bundle`; Git reported 21 refs and complete history.
- Canonical worktree before removal: `C:\boat_project\boatrace-analysis`, `main` at `ff44af4`, two commits ahead of `origin/main`. It is excluded from all deletion commands.
- Protected branches verified: `main`; all five `rescue/*` branches at `42ed768`, `800ba71`, `8c358ca`, `80e72a2`, and `1edacff`.
- Verified `agent/supabase-auth-admin-phase` and `codex-public-roi` are ancestors of `origin/main`.
- Verified unmerged source is preserved: worktree rescue tips `42ed768`, `800ba71`, `8c358ca`; `rescue/unmerged-commits-20260813` contains `d6fb8d5` and `1edacff` for `f11a83d` and `63323df`.
- Approved worktree removal targets, all resolved outside the canonical repository:
  - `C:\Users\tsuyo\AppData\Local\Temp\boatrace-exhibition-cron-fix` (403 status entries; widespread missing files at removal time)
  - `C:\Users\tsuyo\AppData\Local\Temp\boatrace-hamanako-roi-check` (403 status entries; widespread missing files at removal time)
  - `C:\Users\tsuyo\AppData\Local\Temp\boatrace-push-worktree` (127 status entries)
  - `C:\Users\tsuyo\AppData\Local\Temp\boatrace-today-ended-debug-2376b80` (403 status entries; widespread missing files at removal time)
  - `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-cron-audit-worktree` (clean)
  - `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-deploy-worktree` (clean)
  - `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy` (19 excluded temporary/screenshot/probe entries only)
  - `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy-pushsync` (33 excluded pytest/night-audit temporary entries only)
- Approved local branch deletion targets only: `codex-public-roi`, `agent/supabase-auth-admin-phase`, `deploy-main`, `codex/main-motor-fix-20260809`, `codex/pushsync-top-badges`.
- Prohibited and not performed: push, deployment, Render/Supabase changes, scheduled-task changes/runs, execution of task installer/catch-up scripts, removal of main/rescue branches, or manual recursive folder deletion.

## Phase 5 execution

- Executed `git worktree remove --force <path>` sequentially for only the eight approved paths.
- Temp worktrees #2-#5 returned exit 0 and their folders no longer exist.
- OneDrive worktrees #6-#9 returned `failed to delete ... Permission denied`; each command nevertheless removed its Git worktree registration. No manual folder deletion was attempted.
- A post-error substring check initially reported #8 as still registered because the #9 path starts with the full #8 path plus `-pushsync`. A single `git worktree remove --force --force` retry for #8 returned `not a working tree`, confirming #8 was already unregistered. Prevention: final registration checks use complete porcelain worktree blocks, not path substring matching.
- Ran `git worktree prune` successfully after registration removal.
- Deleted only the approved merged branches with safe delete:
  - `codex-public-roi` at `78c1d24`
  - `agent/supabase-auth-admin-phase` at `eb1e530`
- Immediately before forced branch deletion, verified `deploy-main` is an ancestor of `rescue/boatrace-deploy-worktree-wip-20260813`, `codex/main-motor-fix-20260809` is an ancestor of `rescue/boatrace-main-deploy-wip-20260813`, and `63323df` has the same stable patch ID as rescue commit `1edacff`.
- Deleted only the approved preserved branches with forced delete:
  - `deploy-main` at `bdf9cef`
  - `codex/main-motor-fix-20260809` at `300e028`
  - `codex/pushsync-top-badges` at `63323df`
- No push, deployment, scheduler operation, Render/Supabase change, installer/catch-up script execution, or manual recursive folder deletion occurred.

## Phase 5 verification

- `git worktree list --porcelain` contains exactly one worktree: `C:\boat_project\boatrace-analysis`, `main` at `ff44af4`.
- Local branches are exactly `main` plus five `rescue/*` branches:
  - `main` `ff44af4`
  - `rescue/boatrace-cron-audit-worktree-wip-20260813` `42ed768`
  - `rescue/boatrace-deploy-worktree-wip-20260813` `800ba71`
  - `rescue/boatrace-main-deploy-wip-20260813` `8c358ca`
  - `rescue/main-audit-assets-20260813` `80e72a2`
  - `rescue/unmerged-commits-20260813` `1edacff`
- Canonical `main` remains two commits ahead of `origin/main`: `a6d7dc2`, `ff44af4`. It was not reset, merged, pushed, or deleted.
- Protected backup bundle and both task XML files remain present with unchanged observed sizes (7,336,360 / 3,104 / 3,200 bytes).
- Existing canonical status after Phase 5: untracked `reports/kraw_unmatched_accident_rows.csv` plus this required operational log. The plan file remains absent from the main worktree but is preserved on `rescue/main-audit-assets-20260813`.

### OneDrive remnants (not manually deleted)

- `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-cron-audit-worktree`
- `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-deploy-worktree`
- `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy`
- `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy-pushsync`

All four remnant folders still contain files, including stale `.git` files. They are no longer registered Git worktrees. Per the approved instruction, no additional manual deletion was performed.

## Phase 6 final verification

- Confirmed exactly one registered worktree: `C:\boat_project\boatrace-analysis` at `ff44af4` before this documentation commit.
- Confirmed local branches are exactly `main` plus five protected `rescue/*` branches.
- Re-verified the complete-history bundle and both scheduled-task XML backups.
- Confirmed `BoatracePcNightlyPrepare` is `Ready`, targets the canonical batch, has last result 0, and next runs at 2026-08-14 01:00 JST.
- Confirmed only `BoatracePcNightlyPrepare` and `BoatraceLocalSupabaseSync` are active; the other thirteen `Boatrace*` tasks remain disabled.
- Counted the four unregistered OneDrive remnants: 475, 643, 602, and 750 files (2,470 total). No recursive/manual deletion was performed.
- Updated `CLAUDE.md` automation documentation to the verified current state and explicitly prohibited legacy task reactivation scripts.
