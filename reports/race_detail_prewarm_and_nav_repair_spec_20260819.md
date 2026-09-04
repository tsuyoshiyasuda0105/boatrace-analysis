# 作業指示書: レース詳細プリウォーム失敗 + 会員ナビ消失の修理 (Codex CLI 用)

作成: 2026-08-19 朝 / 発注: リッキー / 診断・検品・管理: リン (Claude)
リポジトリ: C:\boat_project\boatrace-analysis (本番 Render + Supabase Postgres)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1105 passed, 1 skipped。割らないこと)

## 問題A: レース詳細ページ生成が Render の朝メンテでだけ全滅する

### 実データ (リン調査済み)
| 日付 | 05:30 メンテ (render_maintenance_detail_v1) | 08:08 selfheal |
|---|---|---|
| 8/14-16 | FAILURE (detail欄 null = 記録なし) | 8/16 SUCCESS |
| 8/17 | FAILURE remaining=168, pages_ok=False | SUCCESS |
| 8/18 | SUCCESS remaining=0 | SUCCESS |
| 8/19 | FAILURE remaining=144, pages_ok=False (attempt 3回とも) | - |

- **ローカルで同一コマンドは完全成功**: `prewarm_race_detail_pages.py --date 2026-08-19
  --missing-only` → 144/144 成功、51.4秒、平均0.334秒/頁、失敗0 (リンが07:33に実測済み。
  本日分はこの実行で復旧済み)。
- つまりスクリプト自体は健全。**Render の 05:30 メンテ文脈でだけ、進捗ゼロで死ぬ**
  (600秒予算 × 3 attempt で remaining が 1 件も減らない = budget超過ではなく起動直後死)。
- 昨日 (8/18) の signal-refresh OOM (コミット 85f2658 で修理) と同型の疑い:
  Render 512MB 制約下でのプロセス起動失敗/OOM。ただし**エラー内容が task_runs に
  記録されておらず確定できない**。これが最大の障害。

### やること
1. [必須] scripts/render_maintenance_scheduler.py の detail フェーズ (run_detail_phase) に
   **失敗診断を追加**: サブプロセスの return code / stderr 末尾 (~500字) / 可能なら peak RSS を
   task_runs.detail に JSON で記録する。85f2658 が render_regular_scheduler 側でやった
   のと同じパターン。tags/pages/integrity の3サブプロセスすべて対象。
2. [必須] 判明している範囲で原因に前もって対処する:
   - メンテ 04:00-05:30 は accident_rebuild 等の重フェーズ直後。コンテナのメモリ残量が
     少ない状態でのサブプロセス起動が疑わしい。prewarm_race_detail_pages 側の
     メモリを下げられる箇所 (バッチサイズ、明示 GC、不要 import) を確認し軽量化。
   - 05:30 に失敗しても 08:08 selfheal が拾う設計は維持。ただし「メンテが失敗を
     報告し毎朝エラーメールが飛ぶ」現状は、診断情報が入るまでは維持でよい
     (握りつぶさない。原因特定が先)。
3. [必須] 回帰テスト: 診断フィールドが失敗時に記録されることをモックで検証。

## 問題B: 会員がレース詳細ページでナビゲーションを失う (ボタン消失)

### 原因 (リン特定済み)
src/web/templates/base.html 11行目:
  {% set cache_neutral_auth = request.endpoint == 'race_detail' %}
レース詳細は共有キャッシュHTML (プリウォームが1レース1枚生成し全ロールに配る) のため、
会員メニュー漏洩防止でヘッダー全体を非会員表示にしている。その結果:
- 会員がレース詳細を開くと **ナビボタンが全部消える**
- ロゴのリンク先も /races になり、**「本日のレース」(/member/today-races) への導線が
  完全に消える** → 発注者から「本日のレースボタンがなくなった・アプリにアクセス
  できない」の報告。実害のある UX 退行。

### やること
4. [必須] 会員がレース詳細ページでも本日のレースへ移動できるようにする。
   制約:
   - 共有キャッシュHTML に会員専用コンテンツを焼き込まない (ゲスト漏洩禁止は維持)
   - レース詳細のリクエスト毎に重い処理を追加しない (昨日の 230秒障害の教訓。
     キャッシュ本文はそのまま使い、ヘッダーだけ毎回サーバー側で描画する方式や、
     セッション状態を返す軽量エンドポイント + クライアント側でナビを出し分ける方式など。
     実装方式は Codex が選んでよいが、per-request コストは数ms級に抑える)
5. [必須] 全ページ共通で、会員ログイン時のヘッダーに **「本日のレース」ボタンを復活**
   させる (ロゴ経由だけでは気づかれなかった)。ヘッダーが溢れて画面外に出た過去が
   あるため (2380fab で9個に整理)、会員表示では「公開ROI」ボタンを外して枠を空ける
   (admin は ROI 系ページを持つため公開ROIは不要。ゲスト表示は変更しない)。
   並び: 本日のレース / バックテスト / プラン申込 / ROI / 月別推移 / 健全度 / 事故率 /
   展示精度 / 管理 (=9個維持)
6. [必須] テスト: 会員で race_detail を開いた際に本日のレースへの導線が存在すること /
   ゲストの race_detail HTML に会員リンクが含まれないこと / 共有キャッシュ経由でも
   漏洩しないこと。既存 test_shared_race_detail_html_is_guest_safe... と整合させる。

## 絶対ルール
- origin/main へ push 禁止・デプロイ禁止 (検品後にリンが実施)
- 本番 Supabase への書込み・スキーマ変更禁止 (調査は読取りのみ)
- 採用ROI戦略 (adopted strategy) の判定結果を変えない
- 稼働中 cron 構成を増減しない
- 作業ログ: reports/race_detail_prewarm_and_nav_repair_work_log_20260819.md
  (変更点 / 測定値 / テスト結果 / 残課題 / コミットID)

## 受け入れ条件
- [ ] detail フェーズ失敗時に rc / stderr末尾 が task_runs.detail に残る (モックテストで保証)
- [ ] prewarm_race_detail_pages のメモリ軽量化の説明と根拠
- [ ] 会員: 全ページ (レース詳細含む) で「本日のレース」に1クリックで到達できる
- [ ] ゲスト: レース詳細 HTML に会員リンクなし (テストで保証)
- [ ] per-request 追加コストが軽量である説明
- [ ] pytest 1105+ passed / push なし / デプロイなし
