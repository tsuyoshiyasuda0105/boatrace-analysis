# 作業指示書: 作業コピー(worktree)の一本化 (P0-1)

作成: 2026-08-13 / 発注者: リッキー / この指示書は単体で完結しています。
目的: **origin/main を唯一の正**とし、作業コピーを `C:\boat_project\boatrace-analysis` の1つに統一する。
未マージのコミット・未保存の変更をすべて救出してから、残りの8つの作業コピーを整理する。

---

## 絶対に守るルール

1. **削除は最後**。バックアップ(Phase 0)と救出(Phase 1)が完了し、発注者の確認を得るまで、いかなる worktree / ブランチも削除しない。
2. **origin/main への push は行わない**。Render が autoDeploy=true で origin/main を監視しており、push は即本番デプロイになる。救出物は `rescue/*` ブランチに置くだけにする(rescue ブランチの push は可)。
3. **force push 禁止。`git reset --hard` は clean な checkout の更新以外で使わない。**
4. **OneDrive 配下に新しい worktree やリポジトリを作らない**(同期による破損リスク)。
5. 稼働中の Windows 定時タスクの変更(Phase 4)は、変更前に現状を XML でエクスポートし、発注者に変更内容を提示して確認を得てから行う。
6. 各フェーズ完了時に、実行したコマンドと結果を作業ログ(`reports/worktree_consolidation_log_20260813.md`)に追記する。

---

## 現状マップ (2026-08-13 に調査・検証済みの事実)

メインリポジトリ: `C:\boat_project\boatrace-analysis` (.git 本体)

### worktree 一覧 (9つ)

| # | パス | ブランチ/HEAD | origin/main との関係 | 未保存の変更 | 処置 |
|---|---|---|---|---|---|
| 1 | `C:\boat_project\boatrace-analysis` | main (dade455) | **55コミット遅れ** | 未追跡6件(下記A) | **正本として残す** |
| 2 | `C:\Users\tsuyo\AppData\Local\Temp\boatrace-exhibition-cron-fix` | detached 80f478a | マージ済み | .claude/* の削除のみ | 削除可 |
| 3 | `C:\Users\tsuyo\AppData\Local\Temp\boatrace-hamanako-roi-check` | detached bf13693 | マージ済み | 同上 | 削除可 |
| 4 | `C:\Users\tsuyo\AppData\Local\Temp\boatrace-push-worktree` | codex-public-roi | **全コミットマージ済み** | 同上 | 削除可(ブランチも) |
| 5 | `C:\Users\tsuyo\AppData\Local\Temp\boatrace-today-ended-debug-2376b80` | detached 4603977 | マージ済み | 同上 | 削除可 |
| 6 | `OneDrive\ドキュメント\New project 2\boatrace-cron-audit-worktree` | detached 69465d3 | HEADはマージ済み | **5 M + 17 ??** (render.yaml, src/db/schema.sql, scripts/ensure_alert_tables.py, src/notifications/subscribers.py, tests/test_race_detail_data_schedule.py + reports群) | **救出後**削除 |
| 7 | `OneDrive\ドキュメント\New project 2\boatrace-deploy-worktree` | deploy-main (bdf9cef) | **未マージ6コミット** | **53 M + 24 ??** (最大の救出対象) | **救出後**削除 |
| 8 | `OneDrive\ドキュメント\New project 2\boatrace-main-deploy` | codex/main-motor-fix-20260809 (300e028) | **未マージ1コミット** | **13 M + 26 ??** (src/web/app.py, collectors 等) | **救出後**削除。⚠️ **稼働中タスクが参照(下記B)** |
| 9 | `OneDrive\ドキュメント\New project 2\boatrace-main-deploy-pushsync` | codex/pushsync-top-badges (cb442f4) | **未マージ1コミット** (63323df) | clean (Permission denied の .tmp_* ディレクトリのみ) | **救出後**削除 |

### 未マージコミットの内訳 (救出対象・検証済み)

- `deploy-main` の6コミット:
  `bdf9cef` Guard motor detail payload mismatch / `87f06cf` Use delegated motor-detail click handling /
  `e94ebe8` Disable browser caching for member race pages / `5359c35` Fix motor detail fallback for cached race pages /
  `0269e82` Fix legacy race id compatibility for motor detail / `f11a83d` feat: track odds fetch status and audit readiness
- `codex/main-motor-fix-20260809` の1コミット: `300e028` Repair lightweight top badge snapshot
- `codex/pushsync-top-badges` の1コミット: `63323df` Prevent recurring login DB exhaustion

完全マージ済みで削除可能なローカルブランチ: `codex-public-roi`, `agent/supabase-auth-admin-phase`

### (A) 正本にある未追跡ファイル (コミットして保全する)

`docs/marketing/`, `playwright_audit/`, `playwright_today_races.png`,
`reports/codebase_audit_20260813.md`, `reports/strategy_scorecard_20260813.md`, `scripts/strategy_scorecard.py`

### (B) 稼働中の Windows 定時タスク (要付け替え)

- `BoatracePcNightlyPrepare` (毎日01:00): `...\New project 2\boatrace-main-deploy\scripts\run_pc_nightly_prepare.bat` を実行
  → **worktree #8 を削除する前に、実行先を正本へ付け替える必要がある。**
  `scripts/run_pc_nightly_prepare.bat` と `scripts/pc_nightly_prepare.py` は origin/main に存在することを確認済み。
- `BoatraceLocalSupabaseSync` (毎日23:45): `C:\projects\New project 2\run_daily_supabase_to_local_sync.ps1`
  → リポジトリ外。今回のスコープ外だが、中身を読んで作業ログに記録すること。
- その他の Boatrace* タスクは全て Disabled (確認済み)。**Disabled のタスクを有効化しないこと。**
  特に `scripts/install_all_tasks.ps1` と `scripts/startup_catchup.py` は旧タスク群を再有効化してしまうため実行禁止。

---

## 手順

### Phase 0: バックアップ (削除より前に必ず)

1. メインリポジトリで全ブランチ・全refのバンドルを作成:
   `git bundle create C:\boat_project\backup-boatrace-all-20260813.bundle --all`
2. 稼働中タスク2つの定義をエクスポート:
   `schtasks /Query /TN "BoatracePcNightlyPrepare" /XML > C:\boat_project\backup_task_BoatracePcNightlyPrepare.xml`
   `schtasks /Query /TN "BoatraceLocalSupabaseSync" /XML > C:\boat_project\backup_task_BoatraceLocalSupabaseSync.xml`

### Phase 1: 未保存変更の救出 (dirty worktree → rescue ブランチにコミット)

worktree #6, #7, #8 のそれぞれで:

1. `.tmp_*` / `.pytest_tmp*` / `__pycache__` / 巨大な画像・スクショ類は救出対象か判断する。
   原則: **ソースコード・設定・テスト・レポート(md/csv)は救出、一時ファイルとスクショは除外**
   (除外したファイルの一覧は作業ログに記録)。
2. rescue ブランチを作ってコミット:
   ```
   git checkout -b rescue/<worktree名>-wip-20260813
   git add -A (除外対象は .gitignore 追記 or add しない)
   git commit -m "WIP rescue from <worktree名> (2026-08-13)"
   ```
   detached HEAD の worktree (#6) も同様に新ブランチを切ればよい。
3. コミット後、`git status --porcelain` が空(または除外物のみ)であることを確認。

worktree #2〜#5 の変更は `.claude/*` の削除のみ(エージェント定義の削除であり救出価値なし)なので救出不要。

### Phase 2: 未マージコミットの整理

1. 未マージ3系統 (`deploy-main` 6件 / `codex/main-motor-fix-20260809` 1件 / `63323df`) は
   **この時点では main に取り込まない**。それぞれの diff を確認し、
   「origin/main に既に別実装で入っていないか」を `git log origin/main --oneline -30` と
   差分内容で照合し、所見を作業ログに記録する。
   (例: 63323df "Prevent recurring login DB exhaustion" は origin/main の
   `c32c6b1 Prevent cron database pool exhaustion` と重複の可能性 — 要確認)
2. 取り込み価値があるものは `rescue/unmerged-commits-20260813` に cherry-pick してまとめる。
   marge/push はしない。**main への取り込みは別作業として発注者が判断する。**

### Phase 3: 正本の更新

1. `C:\boat_project\boatrace-analysis` で未追跡ファイル(上記A)をコミット:
   `git add docs/marketing playwright_audit playwright_today_races.png reports/codebase_audit_20260813.md reports/strategy_scorecard_20260813.md scripts/strategy_scorecard.py`
   `git commit -m "Add audit reports, scorecard tooling, and marketing docs"`
2. `git pull --ff-only origin main` で正本を origin/main に追いつかせる (55+コミット入る)。
   ff-only で失敗した場合は原因を報告し、rebase/merge を勝手に行わない。
3. テストを実行し、成績を記録: `.venv/Scripts/python.exe -m pytest tests/ -q`
   (参考: 更新前は 359 passed / 24 failed。更新後の増減を作業ログに記録。修理はこの作業のスコープ外)

### Phase 4: 定時タスクの付け替え

1. `scripts/run_pc_nightly_prepare.bat` の中身を読み、ハードコードされたパスがあれば報告。
2. **発注者に変更内容を提示して確認を得てから**、実行先を正本に変更:
   `schtasks /Change /TN "BoatracePcNightlyPrepare" /TR "C:\boat_project\boatrace-analysis\scripts\run_pc_nightly_prepare.bat"`
3. 手動で1回実行し、正常終了を確認: `schtasks /Run /TN "BoatracePcNightlyPrepare"` → ログ確認。

### Phase 5: 削除 (発注者の確認後)

1. Phase 0〜4 の完了を発注者に報告し、削除対象一覧(worktree 8個 + ブランチ)を提示して承認を得る。
2. 承認後:
   ```
   git worktree remove --force <パス>   × 8
   git worktree prune
   git branch -d codex-public-roi agent/supabase-auth-admin-phase
   git branch -D deploy-main codex/main-motor-fix-20260809 codex/pushsync-top-badges
     (↑ -D は rescue/unmerged-commits-20260813 に内容が保全されていることを確認してから)
   ```
3. OneDrive 側の残骸フォルダ(空になっていない場合)は発注者に報告し、手動削除は発注者に委ねる。

### Phase 6: 完了検証と報告

- [ ] `git worktree list` の結果が `C:\boat_project\boatrace-analysis` の1件のみ
- [ ] `git branch` が `main` + `rescue/*` のみ
- [ ] `git status` clean / `git log -1` が origin/main と一致
- [ ] `BoatracePcNightlyPrepare` が正本を指し、手動実行成功
- [ ] バンドルバックアップと タスクXML が `C:\boat_project\` に存在
- [ ] 作業ログに: 救出した変更一覧 / 除外した一時ファイル一覧 / 未マージコミットの所見 / テスト成績前後
- [ ] `CLAUDE.md` の「自動化(タスクスケジューラ)」節に現状(生きているタスク2つ、他は Disabled)を反映

## この作業でやらないこと (スコープ外)

- 落ちているテスト24件の修理
- rescue ブランチの main への merge (発注者判断)
- Render / Supabase 側の変更、origin/main への push
- Disabled タスクの有効化・削除
