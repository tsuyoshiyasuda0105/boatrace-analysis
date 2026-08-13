# 作業指示書: 一本化の後片付け (P0-1 完了処理)

作成: 2026-08-13 / 発注者承認済み: OneDriveフォルダ4つの削除を作業AIが実行してよい。
前提: `reports/worktree_consolidation_plan_20260813.md` の Phase 0〜5 完了済み。
検品済み事実: bundleバックアップ有効 / 救出ブランチ5本存在 / git worktree は正本1つのみ。

## タスク 1: OneDrive 残存フォルダ4つの削除 (承認済み)

対象 (フォルダごと完全削除):

1. `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-cron-audit-worktree`
2. `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-deploy-worktree`
3. `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy`
4. `C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy-pushsync`

手順:
1. 削除前の再確認 (必須):
   - `git -C C:\boat_project\boatrace-analysis worktree list` に上記4パスが**含まれていない**こと
   - `git bundle verify C:\boat_project\backup-boatrace-all-20260813.bundle` が成功すること
   - 救出ブランチ5本 (`rescue/*`) が存在すること
   - 4フォルダ内に `*.db` / `*.sqlite` / `.env` (`.env.example` 以外) が**ないこと**を確認。
     見つかった場合は削除を中断し、該当ファイルを報告して指示を待つ。
2. `BoatracePcNightlyPrepare` タスクの実行先が正本を指していること (`schtasks /Query`) を再確認。
3. 削除実行: `Remove-Item -Recurse -Force -LiteralPath <path>`
   - OneDrive のロックで失敗するファイルが出た場合: OneDrive を一時停止 (またはリトライ) して再実行。
     それでも残る場合は残存パス一覧を報告 (部分削除のまま放置しない)。
4. 削除後、4パスが存在しないことを確認して記録。

## タスク 2: 正本の一時ファイル整理

`C:\boat_project\boatrace-analysis` で:
- `.pytest_phase3_20260813/` を削除 (Phase 3 検証の一時ディレクトリ)
- `reports/kraw_unmatched_accident_rows.csv` を削除
  (内容は `rescue/boatrace-main-deploy-wip-20260813` にコミット済みで保全されている)
- 完了後 `git status --porcelain` が空であることを確認

## タスク 3: 救出ブランチの origin への退避 push と、GitHub 上の旧ブランチ削除

1. まず救出ブランチ5本を origin へ push (mainへのpushではないので autoDeploy は発動しない):
   ```
   git push origin rescue/boatrace-cron-audit-worktree-wip-20260813 rescue/boatrace-deploy-worktree-wip-20260813 rescue/boatrace-main-deploy-wip-20260813 rescue/unmerged-commits-20260813 rescue/main-audit-assets-20260813
   ```
2. push 成功を確認**してから**、GitHub 上の古いブランチ3本を削除:
   ```
   git push origin --delete deploy-main codex/pushsync-top-badges agent/supabase-auth-admin-phase
   ```
   (3本とも: マージ済み or 救出ブランチ+bundleに内容保全済みであることを検品確認済み)
3. `origin/main` への push はしない (従来どおり禁止)。

## タスク 4: 作業ログの完成

`reports/worktree_consolidation_log_20260813.md` に追記:
- Phase 5 の実施記録 (削除した worktree / ブランチの一覧と実行コマンド)
- 本指示書 (タスク1〜3) の実施記録
- 最終状態: worktree 1つ / ローカルブランチ = main + rescue×5 / origin = main + rescue×5
追記後、ログと本指示書を main にコミットする (push はしない):
`git add reports/ && git commit -m "Record worktree consolidation completion"`

## 完了条件チェックリスト

- [ ] OneDrive の4フォルダが存在しない (または残存物を報告済み)
- [ ] 正本の `git status --porcelain` が空
- [ ] origin に rescue×5 が存在し、旧ブランチ3本が存在しない
- [ ] 作業ログに Phase 5 + 後片付けの記録があり、コミット済み
- [ ] `origin/main` は無変更のまま
