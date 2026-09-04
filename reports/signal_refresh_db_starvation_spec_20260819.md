# 作業指示書: 昼間シグナル再計算による DB 圧迫 (502連鎖) の恒久修理 (Codex CLI 用)

作成: 2026-08-19 昼 / 発注: リッキー / 診断・検品・管理: リン (Claude)
テスト基準: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1152 passed, 1 skipped。割らないこと)

## 症状 (実データ・リン診断済み)

会員がアプリを開くと 502 が「すぐ出る」。原因は Web ではなく cron:

- render_signal_refresh_XX_exhibition が昼間 **約13分おき**に実行され、
  **1回 225-231秒** (10:17=228s, 10:30=225s, 10:44=229s, 10:58=231s)
- 10:58 の実行直後、Web に transient DB エラー **22件** (handle_500)、
  pool 'pool-1' 12 requests waiting → ユーザー体感は 502/500 バースト
- 11:02 に render_exhibition_detail_refresh と 11:10 の signal_refresh が**同時実行**
  (重複により負荷倍増)
- 同一の再計算はローカル 42秒/SQL 212本 (8/18 計測)。Render では 230秒 =
  **クエリ往復回数がボトルネック** (Render→Supabase の RTT × 212本 + Supabase 共有CPU)
- この慢性負荷が 8/16夜(551件), 8/17夜(384件), 8/18昼(146件)・夜(313件) の
  slow_request と、昨日の「アプリにアクセスできない」の真因でもある

Web 入口は修理済み (cba5028: 会員アクセスでは再計算に入らない)。
残る問題は **cron 側の再計算そのものの重さと重複実行**。

## やること

### [必須1] SQL 往復の削減 (最重要)
- 再計算 1回の SQL 発行数を計測し (現状212本)、レース毎ループの N+1 を
  **集合ベースのクエリへ統合**する。目標: **SQL 30本以下 / Render 実行 60秒以下**。
- 変更対象は市場シグナル再計算経路 (src/web/app.py の market_signals 再構築と
  その配下)。**出力は不変** (シグナル判定ハッシュで前後一致を証明すること)。

### [必須2] 増分再計算
- 展示スロット (XX_exhibition) では、**展示情報が前回計算から更新されたレースが
  無ければ全再計算をスキップ**し、更新があったレースに関係する部分だけ再計算する
  (全レース走査を毎回やらない)。安全のため 1日1回のフル再計算 (朝の preflight/08時台)
  は維持してよい。

### [必須3] 重複実行の防止
- exhibition_detail_refresh と signal_refresh 系が**同時に走らない**よう、
  スケジューラ内で直列化 (単純なアドバイザリロック or 実行中フラグ。
  Supabase の advisory lock (pg_advisory_lock) 使用可)。

### [必須4] 実行時間の記録
- 各 signal_refresh 実行の task_runs.detail に duration_seconds と sql_count
  (計測可能なら) を記録し、明日以降の効果測定を可能にする。

### [必須5] テスト
- 出力同一性 (修正前後のシグナルハッシュ一致) の証拠を作業ログに
- 増分スキップ判定・直列化ロジックの単体テスト

## 絶対ルール
- origin/main へ push 禁止・デプロイ禁止 (リンが実施)
- 本番 Supabase スキーマ変更禁止 / 採用ROI戦略の判定結果を変えない
- render.yaml の cron 構成を増やさない / 今日入れた preflight (7223440)・
  セキュリティ (b49ca49) を壊さない
- 作業ログ: reports/signal_refresh_db_starvation_work_log_20260819.md

## 受け入れ条件
- [ ] SQL 発行数の before/after 計測値 (目標 ≤30)
- [ ] ローカル実行時間の before/after (目標: フル ≤ 15秒 / 増分スキップ ≤ 2秒)
- [ ] シグナル出力ハッシュが前後一致
- [ ] 重複実行防止の実装とテスト
- [ ] pytest 1152+ passed / push なし / デプロイなし
