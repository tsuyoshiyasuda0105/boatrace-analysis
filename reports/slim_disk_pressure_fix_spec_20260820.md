# 作業指示書: 本番ディスク逼迫による適用失敗と会員ページ遅延の修理 (Codex CLI 用)

作成: 2026-08-20 / 発注: リッキー / 診断・検品: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1169 passed, 1 skipped。割らないこと)

## 症状 (リン診断済み・確定)

### 症状A: デルタ適用が失敗
POST /kachisuji/internal/apply-deltas が 500:
```
OSError: [Errno 28] No space left on device:
'/data/kachisuji_slim.db' -> '/data/kachisuji_slim.db.bak'
```
原因: `src/kachisuji/delta_transport.apply_pending_to_slim` は適用前に
`shutil.copy2(slim_db, slim_db + ".bak")` でフルコピーのバックアップを取る。
slim DB は **573MB**、Render の永続ディスクは **1GB** (render.yaml の
コメント通り sizeGB: 1 で作成済み)。573 x 2 = 1146MB > 1GB で必ず溢れる。
同じ全量コピー方式は `scripts/apply_kachisuji_deltas.py` にもある (同罪)。

### 症状B: 会員ページが激遅 (ユーザ報告: 画面が変わらない)
`system_status.slow_request` 2026-08-20T21:33 に
「最遅 /member/today-races: 59.2秒」。ユーザは「本日のレースからバックテストLABを
押しても画面が変わらない」「日付を変えて表示を押しても変わらない」と報告。
実際にはブラウザが 60 秒近く待たされて固まって見えている
(ローカル同一コードでは同操作が即座に成功するので UI マークアップの不具合ではない)。
ディスクが満杯だと SQLite の一時ファイル/ジャーナル作成が失敗・劣化するため、
症状A と同根の可能性が高い。**まず A を直し、その後 B が残るか再測定すること。**

## やること

### [必須1] バックアップ方式をディスク非依存にする
`src/kachisuji/delta_transport.apply_pending_to_slim` の保護方式を、
**全量コピーを使わない**方式へ変更する。以下いずれか、根拠を作業ログに書いて選択:
 (a) SQLite の単一トランザクション内で全デルタを適用し、失敗時は ROLLBACK
     (ATTACH + INSERT OR IGNORE は全て1トランザクションに収められる。
      applied_deltas の記帳も同一トランザクションに含めること)
 (b) 空き容量を事前確認し、足りなければバックアップを取らずトランザクション保護のみ
必須条件:
 - **失敗時に slim DB が中途半端な状態で残らないこと** (原子性の担保)
 - 適用の不変条件は維持: スキーマ検証 / INSERT OR IGNORE / applied_deltas 記帳 /
   二重適用防止
 - `scripts/apply_kachisuji_deltas.py` の同じ全量コピー箇所も同様に修正し、
   両者の方式を揃える (片方だけ直すと将来また踏む)

### [必須2] 空き容量の可視化
 - 適用エンドポイントの戻り値に空き容量 (free_bytes) を含める
 - 空き容量が閾値未満なら、適用を試みる前に明示的なエラーを返す
   (Errno 28 でクラッシュするのでなく、理由の分かる 507 等)
 - プリフライト (scripts/render_maintenance_scheduler.py) に
   **ディスク空き容量チェック**を追加 (slim DB を置くパスの free 容量。
   閾値は 100MB を提案。critical=false でよい)

### [必須3] 回帰テスト
 - 全量コピーを使わないこと / 失敗時に slim が壊れないことのテスト
   (tests/test_kachisuji_delta_transport.py の
    test_apply_restores_backup_on_schema_mismatch を新方式に合わせて更新)
 - 空き容量不足時に Errno 28 ではなく明示エラーになるテスト
 - tests/test_source_regression.py に「apply 系が shutil.copy2 で
   slim 全量コピーをしていない」静的チェック

### [任意・調査] 症状B の残存確認
必須1 適用後も /member/today-races が遅いままなら、遅さの内訳
(SQL 本数・所要) を task_runs か system_status に記録する計測を追加してよい。
ただし**本指示書では UI マークアップ (base.html のナビ・日付フォーム) を変更しない**。
リンの検証でローカルでは正常動作しており、原因はサーバ側の遅延だと確定しているため。

## 絶対ルール
- push 禁止・デプロイ禁止・本番 Supabase 書込み禁止・本番 /data 操作禁止
- 採用ROI戦略の判定結果を変えない / render.yaml の cron 構成を増やさない
  (disk 設定の変更が必要と判断した場合は、**変更せず作業ログに提案として書く**。
   有料オプションなので発注者判断)
- 直近の df4d2e7 / 4ca07ad / 4fdc2f0 / cf07735 を壊さない
- 作業ログ: reports/slim_disk_pressure_fix_work_log_20260820.md
  (方式選択の根拠 / 変更点 / テスト結果 / コミットID)
