# 作業指示書: P1-2 キャッシュ署名をコード由来へ付け替え (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: **687 passed**。P1-1 フェーズBで戦略評価関数は `src/strategies/signals.py` に集約済み。

## 背景と狙い (問題の本質)

派生ページ・シグナルのキャッシュ無効化キーに使う署名が、現状
`src/roi_contract.py::strategy_definition_signature()` = **`sha1(adopted_strategies.md)` の
先頭10桁**。つまり「人間向けMarkdownドキュメントのハッシュ」。

**これが最大のキャッシュ事故源**:
- 戦略の**コードを変えたのに .md を変えなければ署名が同じ** → **古い誤った結果の
  キャッシュがそのまま配信される** (under-invalidation = 実害バグ)。
- 逆に .md の文言だけ直すと無駄に全キャッシュが飛ぶ (churn)。

**狙い**: 署名を **戦略を定義しているコード由来** にする。コードが変われば署名が変わり、
キャッシュが自動で更新される。これで「直したのに古い数字が出る」を根治する。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI 戦略ロジック・予測・DB スキーマ・render.yaml・cron のスケジュールは変更しない。
   本タスクは**キャッシュ署名の算出方法だけ**を変える。
3. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — **687 passed を割らない**。
4. 作業ログ `reports/p1_2_cache_signature_work_log_20260815.md` に記録。
5. コミットは 1〜2個目安。

## やること

### 1. `strategy_definition_signature()` をコード由来に変更

`src/roi_contract.py` の当該関数を、**戦略を定義しているソースコードの内容**から
署名を作るよう変更する。ハッシュ対象は以下の**確定した集合** (存在するものだけ、
ソートして決定的に連結し sha1 → 先頭10桁など現行と同じ長さ):

- `src/strategies/signals.py` (Phase B で集約した評価関数群)
- `src/evaluation/l4_strategy.py`
- `src/evaluation/course_fit_strategy.py`
- `src/evaluation/accident_dent_strategy.py`
- `src/evaluation/omura_124_original_strategy.py`
- `src/roi_contract.py` **自身のバージョン定数** (`ROI_DAILY_CACHE_VERSION` /
  `MARKET_SIGNALS_CACHE_VERSION` / `STRATEGY_PAGE_CACHE_VERSION` の値を署名入力に含める。
  ファイル全体をハッシュすると本関数の編集で自己参照ループ的に毎回変わるので、
  **定数値のみ**を入力に含めること)

**方針の要点**:
- **under-invalidation (古い誤りを配信) を絶対に避ける**方を優先。over-invalidation
  (無駄な再計算) は許容 (安全側)。
- `adopted_strategies.md` への依存は**やめる** (もし後方互換で入れたいなら「追加の
  一入力」として含めるのは可。ただし .md を唯一/主たる根拠にしない)。
- ファイル欠損・読取り失敗時は現行同様に頑健に (例外で落とさない。読めたものだけで
  署名を作り、全滅時のみ `"nosig"` 相当を返す)。
- **プロセス内メモ化**してよい (ソースはプロセス生存中不変。redeploy でプロセスが
  再起動し再計算される)。呼び出し頻度が高いので、毎回のファイル read を避けると軽い。

### 2. 署名が「web と全 scheduler で一致」する契約を壊さない

`strategy_definition_signature` は `src/web/app.py`、`scripts/render_regular_scheduler.py`、
`scripts/prewarm_strategy_pages.py`、`scripts/backfill_roi_race_history.py` から import
される**クロスプロセス契約**。全経路が同じ関数を使うので、roi_contract.py 側の変更だけで
自動的に揃う。**呼び出し側 (app.py 等) の署名利用箇所は変更不要** (シグネチャは
引数なし文字列返しのまま維持すること)。

### 3. テスト

`tests/` に追加:
- 戦略ソース (`src/strategies/signals.py` 等) の内容が変わると署名が変わる
  (tmp コピーやモンキーパッチで「別内容のファイル集合」を渡して差が出ることを検証。
  実ファイルは書き換えない)。
- バージョン定数を変えると署名が変わる。
- 無関係ファイルの変更では署名が変わらない (対象集合外)。
- ファイル欠損時も例外で落ちず、決定的な値を返す。
- 署名が引数なしで呼べて 10桁程度の hex を返す (現行フォーマット互換) こと。

## 受け入れ条件

- [ ] `strategy_definition_signature()` がコード由来 (戦略モジュール群＋バージョン定数) になった
- [ ] `.md` を主たる根拠にしていない / under-invalidation を避ける設計
- [ ] クロスプロセスで同一値 (roi_contract 一元) / 呼び出し側シグネチャ不変
- [ ] 欠損に頑健 / プロセス内メモ化は任意
- [ ] `pytest tests/ -q` 687 passed 維持 + 新規テスト green
- [ ] push していない / 作業ログ提出

## 注意 (デプロイ時の一過性挙動 — 検品メモ)

署名の算出が変わるので、**デプロイ直後は全キャッシュキーが一斉に切り替わり**、
初回アクセスやメンテ/prewarm が新キーで再生成される (一過性の再計算コスト)。
これは想定内 (朝の self-heal / prewarm が吸収する)。リンが検品時に「一過性で済むか」
「署名が誤って `nosig` に落ちていないか」を確認する。

## 検品 (リンが実施)

「.md 依存が外れコード由来になったか」「戦略コード変更で署名が変わることをテストが
握っているか」「app.py 等の利用箇所が無改変か」「nosig に落ちていないか」
「全テスト green か」を照合する。デプロイは発注者承認後。
