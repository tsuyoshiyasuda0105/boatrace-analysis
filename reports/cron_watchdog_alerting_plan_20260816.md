# 作業指示書: cron 自動監視 + 即通知 + 自己修復の仕組み化 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `3a81c9a`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e` (927 passed)。

## 背景と狙い

「cron が失敗/アプリが開けない状態を、人が張り付かずに検知して即通知し、直せるものは
自動で直す」仕組みを作る。今日は 502・朝メンテ全滅・キャッシュ0件を人手で気づいて対応した。
これを自動化する。

**既存の完成部品 (再利用する。作り直さない)**:
- `src/notifications/cron_alerts.py::notify_cron_failure(job, message, *, detail, cooldown_hours)`
  — 管理者へメール (宛先 env `BOATRACE_ERROR_NOTIFY_TO`)、`system_status` 行で
  **同一 job は cooldown 時間に1通**のクールダウン。宛先未設定なら安全に no-op。
- `src/notifications/error_handler.py::install_error_notifier(logger)` — ERROR ログを
  レート制限付きでメール化。
- `src/notifications/mailer.py` — SMTP/Brevo/Resend 自動切替送信。
- 既存の自己修復 `scripts/render_regular_scheduler.py::run_detail_pages_selfheal`、
  `run_yesterday_results_backfill`、`reap_stale_running_tasks`。
- 既存の健全性チェック `scripts/check_post_run_integrity.py`、`check_data_quality.py`。

**現状のギャップ (これを埋める)**:
1. `notify_cron_failure` は maintenance / program-bootstrap にしか配線されていない。
   **regular / odds / exhibition-detail / accident-external / race-detail は失敗しても無通知**。
2. 「今日ハマった障害」(本日の詳細キャッシュ0件・被覆不足、結果取込の穴の拡大、
   cron の連続失敗、プール枯渇の多発) を**まとめて検知して通知+自己修復**する軽い
   ヘルスチェックが無い。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. **render.yaml を変更しない / 新しい cron サービスを追加しない**。監視は**既存の
   regular-cron の tick に相乗り**させる (5分毎 8:00-22:00 JST 稼働)。
3. ROI 戦略・予測・DB スキーマ・収集ロジックは変更しない。
4. **既存の cron 挙動を壊さない**。通知/監視は付随処理で、失敗しても本流 (tick) を止めない
   (例外は握って log)。**クールダウンを必ず使い、メール洪水を防ぐ**。
5. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
6. 作業ログ `reports/cron_watchdog_alerting_work_log_20260816.md`。コミット2〜4個。

## やること

### 1. 各 cron スケジューラの「最終失敗」を通知に配線

`render_regular_scheduler.py` / `odds_scheduler_render.py` /
`refresh_race_detail_after_exhibition.py` / `check_external_accident_snapshot.py` の
**main() が失敗 (例外 or exit!=0 相当) で終わるとき**に `notify_cron_failure(job, msg, detail)`
を呼ぶ。job 名は既存の task 名に合わせる。maintenance / program-bootstrap の既存呼び出しを
**手本**にすること (同じ流儀・同じ cooldown)。
- 既に失敗記録 (record_task_run failure) している箇所の近くで呼ぶ。
- 通知自体の失敗は握りつぶす (本流を止めない)。

### 2. 統合ヘルスチェック (regular-cron の tick に相乗り)

`render_regular_scheduler.py` の tick 内に、軽い**ウォッチドッグ**を追加する
(既存の reap/selfheal を呼んでいる付近)。**1 tick で数クエリ程度の軽さ**に留める。
検知する異常と対応:

| 異常 | 検知条件 (例) | 自己修復 | 通知 |
|---|---|---|---|
| 本日の詳細キャッシュ被覆不足 | 現行版 `race_detail_page:<ver>:<today>` が races の 50%未満 | 既存 `run_detail_pages_selfheal` を起動 | 自己修復が失敗した時のみ通知 |
| 前日結果の取込の穴 | 前日 race で results 欠落が一定数以上 (かつ朝の時間帯) | 既存 backfill を起動 | 修復後も残れば通知 |
| cron 連続失敗 | 直近 N 時間で failure が閾値以上 | なし | 通知 |
| プール枯渇の多発 | `system_status` の transient DB エラー記録が短時間に多発 | なし (プール自己修復は別途稼働) | 通知 |
| ゾンビ running 滞留 | 既存 reaper 後もなお古い running が残る | reaper 再実行 | 残れば通知 |

- **版数はハードコードしない**。`_race_detail_page_cache_key` / `RACE_DETAIL_PAGE_CACHE_VERSION`
  を import するか、`page_html_cache` の LIKE で現行版を数える。
  **今日の v15→v16 のような版数変更後にキャッシュ0件でも、これで自動的に被覆不足を検知して
  自己修復が走る** ようにするのが重要 (今日は手動で先回りした部分の自動化)。
- すべて **cooldown / 1日1回ガード** を使い、暴走・メール洪水を防ぐ。
  被覆自己修復は既存の 30分間隔下限を尊重。
- ヘルスチェックの各異常は `system_status` に記録 (既存パターン、新テーブル禁止)。

### 3. (任意) web プロセスへの error notifier

`src/web/app.py` の起動時に `install_error_notifier(logger)` が呼ばれているか確認し、
**未配線なら配線** (500 ハンドラ等の ERROR がメール化される)。既に呼ばれていれば触らない。
レート制限があるので洪水にはならない。

## テスト (`tests/` に追加)

- 各スケジューラの失敗経路で `notify_cron_failure` が呼ばれる (monkeypatch で確認)。
- ウォッチドッグ: 被覆不足を検知したら selfheal を呼ぶ / 十分なら呼ばない。
- ウォッチドッグ: 異常検知時に (cooldown 外なら) 通知が呼ばれ、cooldown 中は呼ばれない。
- 通知/監視が例外を投げても tick 本流が止まらない。
- 版数非依存 (現行版のキャッシュを数える) の確認。

## 受け入れ条件

- [ ] 全 cron の最終失敗が (宛先設定時に) メール通知される
- [ ] regular-cron tick のウォッチドッグが「今日の障害群」を検知し、直せるものは自己修復
- [ ] キャッシュ版数変更後の被覆0件も自動検知→自己修復
- [ ] cooldown/ガードでメール洪水・暴走なし / 本流を止めない
- [ ] render.yaml 無変更 / 新 cron なし / 既存挙動の回帰なし
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 運用メモ (リンが発注者へ伝える。Codex はコードのみ)

メールが実際に飛ぶには Render 環境変数が必要:
- `BOATRACE_ERROR_NOTIFY_TO` (宛先) — **未設定だと全通知が no-op**
- `BOATRACE_SMTP_*` または Brevo/Resend のキー (送信経路)
これらが未設定でもコードは安全に動く (通知はスキップ、自己修復は動く)。

## 検品 (リンが実施)

「失敗通知が全 cron に配線されたか」「ウォッチドッグが軽量・版数非依存・cooldown 付きか」
「自己修復が既存ヘルパー再利用で暴走しないか」「本流を止めないか」「render.yaml 無変更か」
「テスト green か」を照合。デプロイは発注者承認後。
