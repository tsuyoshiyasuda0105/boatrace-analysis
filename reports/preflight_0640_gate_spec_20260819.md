# 作業指示書: 06:40 公開前プリフライトゲートの新設 (Codex CLI 用)

作成: 2026-08-19 / 発注: リッキー / 診断・検品・管理: リン (Claude)
リポジトリ: C:\boat_project\boatrace-analysis (本番 Render + Supabase Postgres)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1118 passed, 1 skipped。割らないこと)

## 背景 (発注者の要望)

毎朝 04:00-07:00 はメンテ窓 (503)、07:00 に公開される。発注者の要望:
**「06:40 に事前テストを実施してから公開する流れにしたい」**。

さらに今朝、公開直後の TOP にタグ (市場シグナルのバッジ) が無い障害が発覚。
原因: シグナル cache の初回生成 cron が 08:04 で、**07:00-08:04 はタグ空白**になる
(昨日の修理 cba5028 で Web 側の自動再計算を止めたため顕在化)。
→ プリフライトで 06:40 までに当日シグナルを生成すれば、公開時からタグが付く。

## やること

### [必須1] プリフライト実行フェーズ (06:40 JST)
scripts/render_maintenance_scheduler.py に preflight フェーズを追加 (06:40)。
2段構成:

**(a) 生成**: 当日の市場シグナルを cron 側で生成する
  (prewarm_strategy_pages.py --mode realtime 相当。既に成功実績のある経路。
   これで 07:00 公開時からタグが付く)

**(b) 検査**: 以下のチェックリストを実行し、結果を task_runs.detail に
  JSON で全項目記録 (ok/fail + 実測値):

  発注者指定の7項目:
  1. 本日のレースデータ取得済みか (races > 0 かつ entries = races×6)
  2. レース詳細が全レースぶんあるか (page_html_cache の当日カバレッジ = races数)
  3. モーター情報が全レースぶんあるか (motor cache カバレッジ)
  4. タグがついているか (race_detail タグ cache + 当日シグナル cache が pending でない)
  5. 本日のレース(候補ページ)ができているか (/member/today-races が描画可能・候補数)
  6. 事故率は正しく処理されているか (accident snapshot/integrity 直近実行が ok、
     データ鮮度が当日または前日)
  7. バックテストが昨日のデータを取り込めているか (kachisuji slim DB の最大日付 >= 昨日。
     step 27 の daily delta pipeline の結果を確認)

  リン追加分 (8-13):
  8. predictions が全レースぶんあるか (144/144 等)
  9. 当日シグナル cache が存在し空 payload でないか
  10. race_closed_at が全レースに入っているか (欠けると結果取込が遅延)
  11. open incident 数 / 直近12hの cron 失敗数 (異常の早期可視化)
  12. /healthz 応答 (web が生きているか)
  13. DB 接続数に余裕があるか (pg_stat_activity < 45)

### [必須2] ゲート挙動 (公開を止めるか)
- チェックに **critical 失敗** (1, 2, 5 のいずれか = 会員が開いて壊れて見える項目)
  がある場合: その場で該当生成ジョブを 1 回だけ再実行して自己修復を試みる。
  それでも失敗なら: **メンテ窓を最大 07:30 まで延長**できる (env
  BOATRACE_PREFLIGHT_GATE=1 のときのみ。既定は 0 = 延長せず公開)。
  07:30 で必ず公開する (完全性より可用性。無限 503 は禁止)。
- critical 以外の失敗は公開を止めず、アラート (既存の cron alert 経路) にまとめて通知。
- 全結果を system_status にも書き、後から /healthz 系で参照可能に。

### [必須3] テスト
- チェック各項目の ok/fail 判定ロジックの単体テスト (DB はモック)
- critical 失敗 → 再実行 → 延長判定のフローのテスト
- 07:30 キャップが必ず効くテスト

## 絶対ルール
- origin/main へ push 禁止・デプロイ禁止 (リンが実施)
- 本番 Supabase への書込みは task_runs / system_status / 既存 cache への
  通常書込みのみ (スキーマ変更禁止)
- 採用ROI戦略の判定結果を変えない / cron の render.yaml 構成は増やさない
  (既存 maintenance scheduler 内のフェーズ追加で実現する)
- 作業ログ: reports/preflight_0640_gate_work_log_20260819.md

## 受け入れ条件
- [ ] 06:40 にプリフライトが走り、13項目の結果が task_runs.detail に JSON で残る
- [ ] シグナル生成が preflight に含まれ、07:00 公開時からタグが付く設計になっている
- [ ] critical 失敗時の自己修復1回 + (env有効時) 07:30 上限の延長が実装されている
- [ ] pytest 1118+ passed / push なし / デプロイなし
