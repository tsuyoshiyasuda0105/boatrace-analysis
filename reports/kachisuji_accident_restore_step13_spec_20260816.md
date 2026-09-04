# 勝ち筋サーチ Step 13 実装仕様書 — 公式成績ファイルからの事故データ10年分復元

作成: 2026-08-16 リン（Claude Code）/ 発注先: Codex
前提: Step 12 まで実装済み（最新 9e413e3）。schema v5 / 557,617行。

## 背景と目的

事故率が現在ほぼ機能していない:
- 既存ROI互換（`racer_accident_period_stats`）は**直近3週間 3,519件（0.6%）のみ**。
  過去の審査期は期末スナップショット1本しか残っておらず、期間途中のレースから参照できない。
- DB の `race_results.finishing_position` の事故コードは**2025年以降のみ**。

しかし調査の結果、**公式配布の競走成績ファイル（`data/raw/results/`）には
2016年6月〜現在の全期間に事故コードが記録されている**ことを確認済み。
これを解析して**10年分の事故履歴を自前で復元**する。

## 確認済みの事実（調査根拠）

- `data/raw/results/K??????.TXT` が 3,392 ファイル、`k??????.lzh` が 3,392 ファイル存在（計6,784日分）
- 文字コードは **cp932（Shift_JIS）固定幅**
- 着順欄（行頭2スペースの直後）に現れる値の実測分布（60ファイル無作為抽出）:

| 値 | 意味 | 件数 |
|---|---|---:|
| 01〜06 | 通常着順 | 約56,000 |
| F | フライング | 194 |
| L0 / L1 | 出遅れ | 3 |
| K0 / K1 | 失格・妨害失格 | 99 |
| S0 / S1 / S2 | スタート事故 | 532 |

- 行フォーマット実例:
  - 通常: `'  01  4 2787 選手名           30   46  6.91   4    0.16     1.49.7'`
  - 事故: `'  S1  6 4253 選手名           55   20  6.85   5    0.01      .  . '`
  - `'  F   2 4093 選手名           28   50  6.70   2   F0.02      .  . '`
- 各年で F が検出される（2016〜2026、各年8ファイル抽出で31〜43件）
- LZH の解凍は 7-Zip (`C:\Program Files\7-Zip\7z.exe`) 経由（プロジェクト既存の慣習）

## 絶対的な制約（違反禁止）

1. 作成/変更してよいファイル:
   - 新規 `src/features/accident_history.py`（解析・復元ロジック）
   - 新規 `scripts/restore_accident_history.py`（CLI）
   - 新規 `tests/test_accident_history.py`
   - 新規 `docs/kachisuji_accident_restore_step13_result_20260816.md`
   - `src/features/asof_builder.py`（復元データを参照する列の追加のみ）
   - `src/search/roi_search.py` / `src/kachisuji_web/templates/search.html` /
     `static/kachisuji.css`（条件・ラベル追加のみ）
   - 対応テスト
   **本番 `src/web/` は読み取りのみ・変更禁止。`data/boatrace.db` への書込み禁止。**
2. 書込み先は **`data/kachisuji_search.db` の新規テーブルのみ**。
   `racer_accident_period_stats`（本番が使う）には**一切触れない**。
3. **`data/raw/results/` のファイルは読み取りのみ**。解凍が必要な場合は一時ディレクトリへ展開し、
   元ファイルを変更・削除しない。
4. ネットワークアクセス禁止（外部サイトからのデータ取得は明確に禁止）。
   スケジューラ登録・デプロイ・push 禁止。実サーバー起動しっぱなし禁止。
5. **全期間の復元実行はリンが行う**。Codex はサンプル期間（数ヶ月）で検証すること。
6. コミットは main へのローカルコミット1つ。
   メッセージ: `Restore 10-year accident history from official result files (kachisuji step 13)`。

## 実装内容

### Phase 1: 事故コードの定義調査（実装前に必須）

- `src/parsers/official_k.py`（既存の成績パーサ）を**読んで再利用可能か判断**する。
  既存パーサが着順欄を解釈しているなら、それを使う（車輪の再発明を避ける）。
- 公式の**事故点ルール**を、リポジトリ内の既存実装・ドキュメントから確認する
  （`racer_accident_period_stats` を生成した既存スクリプトがあれば、その点数定義を踏襲）。
  **リポジトリ内に根拠が見つからない場合は、事故「点」の付与は行わず、
  事故「件数」と「種別内訳」のみを復元する**（推測で点数を割り当てない）。
  この判断と根拠を結果レポートに明記すること。
- 各コードを「選手責任の事故」として数えるかの分類を決め、docstring に明記:
  - F（フライング）、L0/L1（出遅れ）、K0/K1（失格）は事故として数える想定
  - S0/S1/S2 の扱いは調査して決める（S0 は選手責任なしの可能性がある）
  - **分類の根拠をレポートに記載。不明なものは「不明」として別カウントし、混ぜない**

### Phase 2: 事故履歴テーブルの構築

`data/kachisuji_search.db` に新規テーブル:

```sql
CREATE TABLE IF NOT EXISTS accident_events (
  race_id TEXT NOT NULL,
  race_date TEXT NOT NULL,       -- YYYY-MM-DD
  racer_number INTEGER NOT NULL,
  boat_number INTEGER,
  code TEXT NOT NULL,            -- 'F','L0','L1','K0','K1','S0','S1','S2' 等（原文のまま）
  is_accident INTEGER NOT NULL,  -- 1=選手責任の事故として数える / 0=数えない
  PRIMARY KEY (race_id, racer_number)
);
CREATE INDEX IF NOT EXISTS idx_accident_racer_date ON accident_events(racer_number, race_date);

CREATE TABLE IF NOT EXISTS racer_starts (
  race_id TEXT NOT NULL,
  race_date TEXT NOT NULL,
  racer_number INTEGER NOT NULL,
  PRIMARY KEY (race_id, racer_number)
);
CREATE INDEX IF NOT EXISTS idx_starts_racer_date ON racer_starts(racer_number, race_date);
```
- `racer_starts` は出走記録（事故率の分母）。同じ解析パスで作る。
- append-only。`--rebuild` 指定時のみ該当期間を入替。
- 解析できなかったファイル・行はスキップして warning、最後に件数を報告（黙って落とさない）。

### Phase 3: as-of 事故率の算出（asof_builder への統合）

新しい列（schema_version=6）:
- `bN_accident_rate_period` REAL — **審査期間の事故率**（既存ROIと同じ定義）
  = 期間開始日〜**レース前日**までの事故件数 ÷ 同期間の出走数 × 100
  - 審査期間の区切りは既存ROI互換（5/1〜10/31、11/1〜翌4/30）。
    既存の `_accident_period_start_for_date` を再利用する。
  - **既存ROIとの違い**: 期末スナップショットではなく、**レース前日時点で締めて都度計算**する。
    これにより期間途中のレースでも値が入る。この差分をレポートに明記。
- `bN_accident_count_period` INTEGER — 同期間の事故件数
- `bN_starts_period` INTEGER — 同期間の出走数（分母。信頼性判断に使える）
- 出走数が0の場合は事故率 NULL（0除算しない。0扱いにもしない）
- **as-of 厳守**: レース当日以降の事故は絶対に含めない。既存の verify で検査できるようにする。

既存列（`bN_accident_rate`= 既存ROI互換、`bN_accident_rate_365d`）は**そのまま残す**。

### Phase 4: 検索条件・UI

- 条件キー `accident_rate_period`（min/max）、`accident_count_period`（min/max）を追加。
- 画面の事故率ラベルを3種類に整理し、それぞれ説明とデータ期間バッジを付ける:
  1. 「事故率（審査期・本日判定用）」= 既存 `accident_rate`。📅2026/7〜
  2. 「**事故率（審査期・検証用）**」= 新規 `accident_rate_period`。📅2016/6〜 ← **推奨の既定**
  3. 「事故率（過去1年・参考）」= `accident_rate_365d`
- どれを使うべきかの短い案内文を添える。

### Phase 5: CLI

```
python scripts/restore_accident_history.py --from 2016-06-01 --to 2016-12-31
python scripts/restore_accident_history.py --from 2016-06-01 --to 2026-08-16 --rebuild
python scripts/restore_accident_history.py --stats      # 復元済みの年別件数を表示
```
- LZH しかない日付は 7-Zip で一時展開して解析（元ファイルは変更しない）。
- 進捗を100ファイルごとに出力。処理は日付昇順。

## テスト

1. **パース**: 実ファイル `data/raw/results/K160613.TXT` の既知の事故行
   （`F 2 4093`、`S1 6 4253`）が正しく抽出される。
2. 通常着順（01〜06）が事故として数えられない。
3. 出走数（`racer_starts`）が6艇×レース数で正しく積まれる。
4. **as-of 厳守**: ある選手の事故が「レース前日まで」だけ集計され、当日以降が混入しない
   （合成フィクスチャで境界検証）。
5. 審査期の区切り（5/1、11/1）をまたぐ日付で、期間開始が正しく切り替わる。
6. 出走数0で事故率が NULL（0でない）。
7. append-only（再実行で重複なし）、`--rebuild` で期間入替。
8. 既存の全テスト（ユニット + E2E）がグリーン。

## DoD

1. 全テストグリーン。
2. サンプル期間（例: 2016-06〜2016-12）で復元を実行し、
   - 抽出した事故件数・種別内訳・出走数
   - スキップしたファイル/行の件数と理由
   を結果レポートに記載。
3. 結果レポートに:
   - 事故コードの分類根拠（どれを事故と数えたか、S0/S1/S2 の判断）
   - 事故点を付与したか否かとその根拠
   - 既存ROI定義との差分（期末スナップショット vs 前日締め）
   - **全期間復元はリンが実行する旨**
   - 既知の制限
4. ローカルコミット1つ（push しない）。

## 注意

- **推測で数字を作らない**。分類が不明なコードは「不明」として別集計し、事故に混ぜない。
- 既存パーサ（`src/parsers/official_k.py`）が使えるなら再利用する。
  使えない場合は理由をレポートに書くこと。
- 固定幅パースは年によってフォーマットが変わる可能性がある。
  **年ごとにサンプル検証**し、崩れがあれば報告すること（黙って欠測にしない）。
