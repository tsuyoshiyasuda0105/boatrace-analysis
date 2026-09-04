# 作業指示書: /api/market-signals の重い再計算を会員リクエストから排除 + 通知の重複抑制 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `223b303`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e`。

## 背景 (リンが実測で切り分け済み)

会員へ**大量のエラーメール**が届いている。本番調査:
- `slow_request`: 最遅 **`/api/market-signals` 277秒**、448件。
- `transient_db_error` / `postgres pool checkout failed` が発生 → プール枯渇 → エラーメール。
- 実測: `/api/market-signals?...&recompute=1` は **107秒 (db 0.0ms = CPU 計算 192/192 レース)**。
- 原因: **P1-2 の署名変更 (fdc09d55c9→37ba2789bd) で現署名の market_signals キャッシュが
  今日1件しか無く**、過去日を開くたびに**会員リクエスト内で重い再計算 (100秒超)** が走り、
  gunicorn timeout(120s) 前後で接続を占有 → プール枯渇 → checkout 失敗 → ERROR ログ → メール。
- これはレース詳細で解決済みの「重い再計算を会員リクエストでやらない」問題と同型。

## ゴール

1. **会員(人間)の `/api/market-signals` リクエストで重い再計算を絶対に走らせない。**
   キャッシュ/last-good が無ければ「準備中」相当を即返し、生成は cron/prewarm に任せる。
2. **同種エラーの通知メールが洪水にならない** (重複抑制/クールダウンの改善)。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI 戦略・予測・DB スキーマ・render.yaml・収集は変更しない。
   **market_signals の計算結果(数値)は変えない** — 呼ぶタイミングと配信方法だけ直す。
3. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
4. 作業ログ `reports/market_signals_no_recompute_and_alert_dedup_work_log_20260816.md`。
   コミット2〜3個。

## やること

### 1. `/api/market-signals` を「会員リクエストで再計算しない」構造に

- 該当エンドポイント (app.py の market signals API) を、レース詳細の
  `EXPENSIVE_RECOMPUTE_TRIGGERS` / `_effective_force_recompute()` と同じ方針にする:
  - **人間の通常リクエスト**: 現署名キャッシュ → 無ければ **last-good スナップショット
    (`market_signals:last-good:<date>`, 署名非依存) を返す** → それも無ければ
    **「準備中」相当の軽量応答 (200, 空/pending, 自動再取得を促す)** を即返す。
    **重い全レース再計算に絶対入らない。**
  - **prewarm/cron (`recompute=1` + 許可トリガー)**: 従来どおり再計算して cache 保存。
- 画面(トップの市場シグナル表示や ROI 系)から呼ばれる経路も、**人間リクエストでは
  再計算に落ちない**ことを保証する。既に last-good フォールバックがあるなら、
  「現署名キャッシュ欠落時に live 再計算へ落ちる分岐」を潰す。

### 2. cron 側で現署名の market_signals を継続的に温める

- 既存 `scripts/prewarm_strategy_pages.py --mode signals` / 
  `refresh_race_detail_after_exhibition.py` のシグナル更新経路が、**現署名で当日+直近を
  prewarm し続ける**ことを確認。署名変更後にキャッシュ0でも、cron が現署名で埋めるので
  会員は再計算に落ちない、という状態を担保 (既にあるなら確認のみ)。

### 3. 通知の重複抑制 (メール洪水対策)

- `src/notifications/error_handler.py` の通知キー (レート制限の単位) が、
  **メッセージ全文 (可変の stats 込み) でキー化している**と、`pool checkout failed
  stats={...}` のように毎回文言が変わり**重複排除が効かず洪水**になる。
  → **正規化したキー (logger 名 + エラー種別/先頭の安定部分、数値stats を除去)** で
  レート制限するよう改善。同種エラーは既定クールダウン(1時間)に1通へ。
- `cron_alerts.notify_cron_failure` は job 単位でクールダウン済みなので、それに倣う。
- **通知を止めるのではなく、同種を束ねる**。重大な新種は従来どおり通知される。

## テスト (`tests/` に追加)

- 人間リクエストでキャッシュ/last-good 欠落時、**重い再計算関数が呼ばれず**準備中/last-good が返る。
- prewarm/cron トリガー時は従来どおり再計算する。
- error_handler: 可変 stats を含む同種エラーが**正規化キーで1通に束ねられる** (別種は別通知)。
- market_signals の**計算結果自体は不変** (呼び方を変えても数値は同じ)。

## 受け入れ条件

- [ ] 会員の /api/market-signals で重い再計算が起きない (テストあり)
- [ ] last-good/準備中フォールバックで即応、cron/prewarm が現署名を温める
- [ ] 同種エラーメールが1時間1通に束ねられる (正規化キー、テストあり)
- [ ] market_signals の数値・ROI 定義は不変 / 通常表示が重くならない
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「人間リクエストで再計算に落ちないか (プール枯渇の根絶)」「last-good/準備中が返るか」
「エラーメールが同種で束ねられるか」「数値・ROI 定義が不変か」「cron が現署名を温めるか」
「テスト green か」を照合。デプロイは発注者承認後。
