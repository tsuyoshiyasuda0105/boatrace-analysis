# 作業指示書: 朝メンテのページ生成が Render で0枚になる問題 (OOM疑い) の根治 (Codex CLI 用)

作成: 2026-08-17 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `c7c98b6`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e` (1017 passed)。

## 背景 (リンが実測で切り分け済み)

2026-08-17 朝、会員が「本日のレース詳細が全て準備中・TOPにフラグ無し」。調査:
- `render_maintenance_detail_v1` (8/17) failure。detail:
  `{"tags_ok": true, "pages_ok": false, "partial": false, "remaining": 168, "attempt_count": 3}`
- **tags は 168 完成、pages は 0/168 (1枚も生成せず)。3回試行して全て 0**。
- `partial: false` = 徐々にタイムアウトではなく、**pages 生成が開始直後にクラッシュ/即死**。
- **リンがローカルで同じ `scripts/prewarm_race_detail_pages.py --date 2026-08-17` を実行したら
  168枚成功** → **コードは正常。Render 環境 (cron, 512MB 制約) 特有の失敗**。
- 予算: `DETAIL_TAG_BUDGET_SEC=600`, `DETAIL_PAGE_BUDGET_SEC=600`, `timeout=900`
  (`render_maintenance_scheduler.py`)。
- **最有力仮説: メモリ不足 (OOM) で pages プロセスが起動直後に kill**
  (例: `prewarm_race_detail_pages` が `src.web.app` を import する際に予測モデル
  (LightGBM cascade/calibrator 等) をロードして 512MB を超える、等)。
- **Render ログは未取得** (発注者判断で仮説ベースで進める)。

## ゴール

**Render (512MB) でも朝メンテのページ生成が 0 枚で終わらない**ようにする。
根本はメモリ削減。加えて、万一失敗しても穴が残らない安全網を確実にする。
**ページの生成結果 (HTML 内容) は変えない。**

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI・予測ロジック・DB スキーマ・render.yaml・収集は変更しない。
   **生成される page HTML はバイト同等** (メモリの使い方だけ変える。特性化で担保)。
3. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
4. 作業ログ `reports/pages_prewarm_oom_fix_work_log_20260817.md`。コミット2〜3個。

## やること

### 1. pages プレウォームのメモリ footprint を調べて削減

- `scripts/prewarm_race_detail_pages.py` (と、それが import/呼び出す
  `src.web.app` の page 生成経路) を読み、**起動時・各レース処理時に何がメモリに載るか**
  を特定する。特に:
  - **import 時に予測モデル (cascade/calibrator/ranker 等) をロードしていないか。**
    ページ HTML は**既に DB にある予測 (predictions テーブル) を読むだけ**で描けるはず。
    もしモデルロードが走っているなら、**pages プレウォームではモデルを読み込まない**
    経路にする (遅延ロード化 / cached-only の描画にする)。
  - 各レース処理後に**大きなオブジェクトを解放** (ループ内で参照を切る、必要なら
    `gc.collect()` を一定間隔で)。
- **可能ならピークメモリを計測して作業ログに記録** (tracemalloc または
  resource.getrusage 等。Windows で取れる範囲でよい。取れなければ import 時に
  ロードされるモジュール/モデルの有無を静的に示す)。

### 2. サブバッチ化 + メモリ解放 (防御)

- pages 生成を**小さいサブバッチ (例: 20〜30 レースごと) に区切り、バッチ間で
  接続を閉じ、必要なら gc**。1レースごとの逐次保存 (既存) は維持。
- **既存の `--budget-sec` の刻みと両立**。タグが予算を食い切って pages に予算が
  残らない、という配分問題があれば、予算配分も見直す (例: pages に十分残す)。

### 3. 安全網: 失敗しても穴を残さない

- 朝メンテの pages が失敗 (0枚) でも、**日中の regular-cron self-heal
  (`render_detail_pages_selfheal`) が確実に埋める**こと。self-heal も同じ pages
  スクリプトを使うので、**1 と 2 の改善が self-heal にも効く**。
- self-heal が**被覆 100% になるまで (間隔下限を守りつつ) 繰り返し埋める**設計に
  なっているか確認。0枚 → 少しずつ埋まる、を許容する。
- **朝メンテが pages 失敗でもフェーズを「部分成功」で継続**し、他 (snapshot/integrity)
  や後続を止めないこと (既存方針の維持・確認)。

### 4. (調査のみ) OOM の確証

- Render ログ無しなので断定はしない。**import 時のモデルロード有無**という
  静的事実だけでも OOM 仮説の裏付けになるので、作業ログに明記する。

## テスト (`tests/` に追加)

- pages プレウォームが**予測モデルをロードせずに** (cached predictions のみで) HTML を
  生成できることを検証 (モデルロード関数が呼ばれないことを monkeypatch で確認)。
- サブバッチ処理でも**生成 HTML が従来とバイト同等**であること (特性化)。
- self-heal が被覆不足の日に (間隔下限を守り) 繰り返し pages を埋められること。
- 既存の budget/resumable 挙動の回帰なし。

## 受け入れ条件

- [ ] pages プレウォームがモデルをロードしない (メモリ削減) / ピーク or ロード有無を記録
- [ ] サブバッチ + メモリ解放で 512MB 級でも生成継続できる設計
- [ ] 朝メンテ失敗時も self-heal が 100% まで埋める安全網
- [ ] 生成 HTML はバイト同等 (特性化テスト)
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「pages がモデルをロードしなくなったか (メモリ削減)」「HTML がバイト同等か」
「サブバッチ/解放が入ったか」「self-heal の埋め切り安全網」「予測/ROI/スキーマ不可侵」
「テスト green か」を照合。デプロイは発注者承認後。ローカルで pages プレウォーム再実行し
168枚生成と内容一致を再確認する。
