# 勝ち筋サーチ Step 15 仕様書 — 平均STの10年分自前復元

作成: 2026-08-16 リン（Claude Code）/ 発注先: Codex
前提: Step 13-14 で事故履歴の復元基盤が完成（`src/features/accident_history.py`、
`accident_events` / `racer_starts` テーブル、`data/raw/results/` の成績ファイル完備）。

## 背景と目的

平均ST（avg_start_timing）が **2025年5月以降しか存在しない**（元データ `race_entries` の
時点で欠損、番組表ファイルにも非掲載）。しかし公式競走成績ファイルには**各レースの実測ST**が
記録されているため、これを集計して**平均STを10年分（2016-06〜）自前復元**する。
これによりユーザーの平均STを使う手法（へこみ型、多摩川1-3等）が全期間で検証可能になる。

## 確認済みの事実（調査根拠）

- 成績ファイル `data/raw/results/K??????.TXT`（cp932固定幅）の各着順行に**実測ST**が入っている:
  - 通常行例: `'  01  4 4948 選手名   46   36  6.75   4    0.14     1.48.7'`
    → 着順=01, 艇番=4, 登録番号=4948, ... **進入コース=4, ST=0.14**
  - フライング行例: `'  F   3 3211 選手名   55   43  6.67   3   F0.02'`
    → ST欄は `F0.02`（フライング。負のスタート = 早すぎ）
  - 出遅れ（L）は ST欄が `.`（欠測）のことがある
- 既存パーサ `src/features/accident_history.py::_parse_result_row`（`src/parsers/official_k.py`
  由来）が**既にこの行を解析している**。ST値も同じ行から取得できる。
- 既存の `racer_starts` テーブルが「選手×レース×日付」の出走記録を持っている（分母に流用可能）。

## 絶対的な制約（違反禁止）

1. 変更/作成してよいファイル:
   - `src/features/accident_history.py`（ST抽出の追加。既存の事故ロジックは壊さない）
     もしくは新規 `src/features/start_timing_history.py`（どちらでもよい。判断を結果レポートに明記）
   - `scripts/restore_accident_history.py`（ST同時復元の追加）または新規 `scripts/restore_start_timing.py`
   - `src/features/asof_builder.py`（平均ST列の算出元を復元テーブルに切り替え）
   - `src/search/roi_search.py` / `templates/search.html` / `static/kachisuji.css`
     （必要ならラベル・期間バッジの更新のみ）
   - 対応 `tests/`、新規 `docs/kachisuji_avgst_restore_step15_result_20260816.md`
   **本番 `src/web/` は読み取りのみ・変更禁止。`data/boatrace.db` への書込み禁止。**
2. 書込み先は **`data/kachisuji_search.db` の新規/既存の検索用テーブルのみ**。
   `data/raw/results/` は読み取りのみ（LZHは一時展開、元ファイルを変更しない）。
3. ネットワーク禁止。スケジューラ登録・デプロイ・push 禁止。実サーバー起動しっぱなし禁止。
4. **全期間の復元・再生成はリンが実行する**。Codex はサンプル期間で検証すること。
5. コミットは main へのローカルコミット1つ。
   メッセージ: `Restore 10-year average start timing from official result files (kachisuji step 15)`。

## 実装内容

### Phase 1: ST イベントの抽出・格納
`data/kachisuji_search.db` に新規テーブル:
```sql
CREATE TABLE IF NOT EXISTS start_timing_events (
  race_id TEXT NOT NULL,
  race_date TEXT NOT NULL,       -- YYYY-MM-DD
  racer_number INTEGER NOT NULL,
  boat_number INTEGER,
  course_number INTEGER,         -- 進入コース（ST行の値）
  start_timing REAL,             -- 実測ST（Fは負値。欠測はNULL）
  is_flying INTEGER NOT NULL,    -- 1=フライング(F)
  is_late INTEGER NOT NULL,      -- 1=出遅れ(L)
  PRIMARY KEY (race_id, racer_number)
);
CREATE INDEX IF NOT EXISTS idx_st_racer_date ON start_timing_events(racer_number, race_date);
```
- **STの符号規約を明確に**し docstring とテストで固定する:
  - 通常のST `0.14` は正の値（スタート遅れ方向）
  - フライング `F0.02` は**フライング＝早すぎ**。`start_timing = -0.02`, `is_flying=1` とする
    （公式の平均ST計算にフライングをどう含めるかは後述）
  - 出遅れや欠測（`.`）は `start_timing=NULL`, `is_late`（Lなら1）
- 既存の事故復元と**同じパース経路を再利用**する（`_parse_result_row` からST欄を取り出す）。
  車輪の再発明をしない。行の解析を二重実装しないこと。
- append-only。`--rebuild` で期間入替。解析不能行はスキップして warning + 最後に集計。

### Phase 2: as-of 平均ST の算出（asof_builder, schema_version=7）
新しい列（`bN_avg_st` は既存だが、算出元を復元データに切り替える。互換のため既存列名を維持）:
- `bN_avg_st` REAL — **その選手の過去平均ST**。
  - 集計窓: **レース前日までの直近180日**（へこみ型手法が「過去180日平均ST」を使うため、
    これに合わせる。窓の定義を docstring に明記）
  - **平均STの計算に含める走**: フライング(F)は含めるか除外するか、
    公式・実務の慣行を調査して決める。**判断できない場合はデフォルトで「Fは除外し、
    通常STのみで平均」とし、その旨を明記**（Fを-0.02等で混ぜると平均が歪むため）。
    欠測（NULL）は当然除外。
  - 母数が一定数未満（例: その窓で有効ST < 4走）のときは信頼性が低いので、
    `bN_avg_st_n`（有効走数）も列として持ち、NULLにはしないが n を併記できるようにする。
  - 出走記録が0または有効STが0走なら `bN_avg_st = NULL`（0にしない）。
- **as-of厳守**: レース当日以降のSTは絶対含めない。既存verifyで検査可能にする。
- 既存の 2025-05以降の `race_entries.avg_start_timing`（番組表由来の公式平均ST）とは**定義が違う**
  （公式は審査期通算、こちらは直近180日）。**どちらを bN_avg_st とするか**を決める:
  - 推奨: 復元した「直近180日平均ST」を `bN_avg_st` とし全期間で使う。
    番組表由来の値は参考列 `bN_avg_st_official`（2025-05以降のみ）として別に残してもよい。
  - この設計判断と、2つの定義の差を結果レポートに明記。

### Phase 3: 検索・UI
- 既存の平均ST条件（`avg_st` min/max、艇間比較の `avg_st`）が復元値で全期間動くようにする。
- 画面の平均STのデータ期間バッジを📅2016/6〜相当に更新（実測に合わせる）。
- 「直近180日平均ST（自前集計）」であることと、母数nの見方を短く案内。

## テスト
1. **パース**: 既知の成績行から ST が正しく取れる（通常0.14、F0.02→-0.02+is_flying、L/欠測→NULL）。
2. **as-of厳守**: ある選手の平均STが「レース前日まで180日」だけで計算され、当日以降が混入しない
   （合成フィクスチャで境界検証）。
3. Fの扱いがデフォルト規約どおり（除外）で、平均が通常STのみで計算される。
4. 有効走数 `bN_avg_st_n` が正しく、有効0走で `bN_avg_st=NULL`。
5. 艇間比較 `avg_st`（へこみ型の「隣接艇より0.02遅い」）が復元値で判定できる。
6. append-only、`--rebuild` 期間入替。
7. 既存の全テスト（ユニット + E2E）がグリーン。既存の事故復元テストが壊れないこと。

## DoD
1. 全テストグリーン。
2. サンプル期間（例: 2020-01〜2020-06）で復元を実行し、
   ST抽出件数 / F・L・欠測の内訳 / スキップ件数 / 平均STが入ったレース率 を結果レポートに記載。
3. 結果レポートに:
   - STの符号規約とFの扱い（含める/除外）の判断根拠
   - 集計窓（180日）の定義
   - 番組表由来の公式平均STとの定義差
   - **全期間復元・再生成はリンが実行する旨**
   - 既知の制限
4. ローカルコミット1つ（push しない）。

## 注意
- **推測で数字を作らない**。Fの平均への含め方が不明ならデフォルト（除外）を採り、明記する。
- STの列位置（固定幅）が年によって変わる可能性がある。**年ごとにサンプル検証**し、
  崩れがあれば報告（黙って欠測にしない）。
- 既存の事故復元パイプラインと同じファイルを読むので、**1回のパースでST・事故・出走を
  同時に取得できるなら統合してよい**（二重パースを避ける）。統合したか否かをレポートに明記。
