# 勝ち筋サーチ Step 7 実装仕様書 — オッズ条件・不等号比較UI・動線再設計

作成: 2026-08-16 リン（Claude Code）/ 発注先: Codex
前提: Step 1〜6 実装済み（最新コミット 16489b9、全182テストグリーン、DB=schema v4 557,617行）。

## 概要（3本柱）

1. **オッズ条件**: 買い目（3連単）の T-5分オッズ帯を検索条件に追加
2. **不等号比較**: 艇間比較を「差なしの不等号」だけでも組めるように（例: 1号艇の平均ST ≥ 2号艇）
3. **動線再設計**: 検索ボタンと結果が遠い問題を、スティッキー操作バー＋結果追従で解消

## 絶対的な制約（違反禁止）

1. 変更してよいファイル:
   - `src/features/asof_builder.py` または新規 `src/features/odds_sync.py`（オッズ同期）
   - `scripts/build_asof_features.py`（--sync-odds 追加時のみ）
   - `src/search/roi_search.py` / `src/search/strategies.py`
   - `src/kachisuji_web/app.py` / `templates/search.html` / `static/kachisuji.css`
   - `tests/`（対応テスト）
   - 新規 `docs/kachisuji_odds_ux_step7_result_20260816.md`
   - 運用記録として `docs/handoff.md` への追記は可
   他の既存プロダクトファイルは変更禁止。
2. `data/boatrace.db` は読み取りのみ。書込みは `data/kachisuji_search.db` の**新規オッズテーブルのみ**
   （asof_race_features 本体は変更しない。列追加もしない）。
   全期間のオッズ同期実行はリンが行うため、Codex はサンプル期間のみ同期して検証する。
3. 実サーバー起動しっぱなし禁止（test_client / E2E はポート8090で終了時kill）。
4. ネットワーク・スケジューラ・デプロイ・push 禁止。
5. コミットは main へのローカルコミット1つ。
   メッセージ: `Add odds condition, inequality compare, sticky layout (kachisuji step 7)`。

## 1. オッズ条件

### 1-1. データ調査（実装前に必須）
`data/boatrace.db` の `odds_trifecta`（snapshot_label: 'T-5min'/'T-1min'/'final' 等）について:
- スナップショット別の保有期間（最古〜最新日付）と対象レース数を調査
- T-5min が薄い期間は final で代替できるか（両方の件数を報告）
- 調査結果を結果レポートに表で記載し、UIの📅バッジ表記（例「📅 2026/5〜」）を実測に合わせる

### 1-2. オッズ格納（kachisuji_search.db 内に新テーブル）
```sql
CREATE TABLE IF NOT EXISTS odds_snapshot (
  race_id TEXT NOT NULL,
  combination TEXT NOT NULL,      -- '1-2-3' 形式（3連単）
  snapshot TEXT NOT NULL,         -- 'T-5min' / 'final'
  odds REAL NOT NULL,
  PRIMARY KEY (race_id, combination, snapshot)
);
```
- 同期コマンド: `python scripts/build_asof_features.py --sync-odds --date-from ... --date-to ...`
  （boatrace.db から読み、上記へ append-only。既存キーはスキップ。--rebuild 併用時のみ入替）
- T-5min と final の両方を格納（あるものだけ）

### 1-3. 検索条件（**ユーザー決定: 確定オッズを既定とする**）
条件JSONに追加:
```json
"odds": {"snapshot": "final", "min": 5.0, "max": 15.0}
```
- **既定・UIの主対象は 'final'（確定オッズ）**。ユーザーの明示決定。
  T-5min はデータが存在すれば選択肢として残してよい（任意）。
- 適用対象は**買い目そのもの**（bet が sanrentan のときのみ有効）。
  bet が tansho / nirentan でオッズ条件が指定されたら日本語エラー
  （「オッズ条件は現在3連単のみ対応しています（単勝・2連単のオッズは未収集）」）。
- 判定: `odds_snapshot` を race_id + 買い目組合せ + snapshot で結合し min/max フィルタ。
  **該当レースにオッズ行が無い場合は condition_null 除外**（既存規則と同じ）。excluded の内訳に出す。
- 性能: オッズ条件があるときだけ JOIN（無いときの検索性能を落とさない）。PKで引けるので軽い。

### 1-4. 照合（strategies）と正直な注意書き
- **確定オッズはレース確定後にしか分からない**ため、本日照合では判定不能:
  前日確定条件がすべて合致していれば `pending` に分類し、`undetermined_columns` に `odds` を入れる。
- UI のオッズ条件の近くに注記を表示:
  「確定オッズは締切前には分かりません。過去検証・傾向分析用の条件です。当日照合では未確定扱いになります」
  （T-5min を選んだ場合のみ締切直前に判定可能である旨も添える）
- UI の凡例・バッジ: オッズ条件に ⏱ と 📅（実測期間）を付ける。

## 2. 不等号比較（UI改善）

- 現行 compare エンジンは margin>=0 の ge/le に対応済み。**エンジン変更は原則不要**。
- UI を「[N号艇] の [指標] が [M号艇] [以上/以下]（差: X 任意・既定0）」の形に変更:
  - 差の入力欄は任意。空なら margin=0（純粋な不等号 ≥ / ≤）
  - 例文プレースホルダ「例: 1号艇の平均STが2号艇以上」
  - 単位表示（pt/秒/歳）は差入力欄の横に維持
- 既存の保存済み手法（margin付き）が壊れないこと。

## 3. 動線再設計（UX）

### 3-1. スティッキー操作バー（最重要）
- 画面下部に固定（position: fixed; bottom: 0）の操作バーを新設:
  - 「🔍 検索」「★ 保存」ボタン（従来のボタンはバーへ移設。二重配置しない）
  - 設定中の条件数バッジ（例「条件 7個」）
  - 検索後は直近結果のミニKPI（回収率・N）をバー内に表示。クリックで結果へスクロール
  - 検索中はスピナー/「検索中…」表示、二重送信は引き続き抑止
- バーの高さぶん、本文下部に padding を確保（最下部の条件が隠れないように）
- ダーク/ライト両テーマで背景・境界を明示（透け防止）

### 3-2. 結果の追従と自動スクロール
- デスクトップ幅（>=880px）: 右カラムの検索結果パネルを `position: sticky; top: 0`（または適切なtop）
  でスクロールに追従させ、条件編集中も結果が見えるようにする
- モバイル幅: 検索完了時に結果セクションへ `scrollIntoView`（smooth）。
  `prefers-reduced-motion` 指定時は瞬間移動
- 結果更新時に一瞬ハイライト（新しい結果と気づける控えめな演出。テーマ両対応）

### 3-3. E2E 検証（Playwright）
- 1280px・390px 両方で: 最下部までスクロールしても検索ボタンが視認・クリック可能
- 検索後、モバイル幅で結果が viewport 内に入る
- スティッキーバーのミニKPIが検索結果と一致
- 既存49シナリオが引き続き全pass

## テスト（ユニット＋E2E）

1. オッズ同期: サンプル期間で odds_snapshot が生成され、append-only・--rebuild 入替が働く
2. オッズ条件: min/max 境界、オッズ行なし→condition_null、単勝/2連単指定→日本語エラー、
   snapshot 切替、JOIN有無で他条件の結果が変わらないこと
3. 照合: オッズ条件つき手法が、オッズ未取得日に pending / 取得済で confirmed になる
4. compare: margin省略=0 の等価性（{"op":"ge","margin":0} と同一結果）
5. UI: スティッキーバー・追従・自動スクロールのE2E（上記3-3）

## DoD

1. 全テストグリーン（既存182 + 新規）。
2. 結果レポートに: オッズ保有期間の調査表 / 変更ファイル / UI変更のビフォーアフター説明 /
   既知の制限（単勝・2連単オッズ未収集、オッズ期間の短さ）/ 全期間オッズ同期はリンが実行する旨。
3. ローカルコミット1つ（push しない）。
