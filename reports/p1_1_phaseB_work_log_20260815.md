# P1-1 フェーズB 作業ログ

作業日: 2026-08-15

## 制約

- Phase A の17ケース・9関数の golden 期待値は変更しない。
- 危険地帯、スキーマ、ROI数値、予測、`render.yaml`、cron、通知には触れない。
- ローカル `main` のみ。push・deployは行わない。
- 1関数=1コミットを基調とし、各コミット後に全687テストを実行する。

## 基準確認

- 初回の通常実行は共有Windows一時領域の `PermissionError` により `644 passed / 43 setup errors`。製品アサーション失敗なし。
- リポジトリ内専用 `--basetemp .pytest_tmp_p1_1_phaseB_20260815` で再実行し、変更前 `687 passed` を確認。

## モジュール構成

- `src/strategies/__init__.py`: 純粋戦略評価パッケージ。
- `src/strategies/signals.py`: Phase Aで特性化済みの純粋シグナル評価関数。

## 関数別記録

### `_detect_niche_signals`

- `src/web/app.py` のトップレベル定義を本体そのままで `src/strategies/signals.py` に移動。
- `src/web/app.py` は同名関数をモジュールimportし、既存呼び出しは変更なし。
- 閉包依存: なし。
- Phase Aテストはimport元だけを新モジュールへ変更。golden期待値は不変。
- コミット: `397dd1e`。
- コミット後全テスト: `687 passed`。

### `_compute_tetsuban`

- `market_signals_for_date` 内のネスト定義を本体そのままで `src/strategies/signals.py` に移動。
- `src/web/app.py` は同名関数をモジュールimportし、既存呼び出し引数 `(base, rn)` は変更なし。
- 閉包依存: なし。
- Phase Aテストは呼び出し元だけを新モジュールimportへ変更。golden期待値は不変。
- コミット: `d3b632e`。
- コミット後全テスト: `687 passed`。

### `_evaluate_l4_general_200`

- 互換用の常時 `None` evaluatorを本体そのままで `src/strategies/signals.py` に移動。
- `src/web/app.py` は同名関数をモジュールimport。現行app内に呼び出しはなく、互換hookの公開位置だけを置換。
- 閉包依存: なし。
- Phase Aテストは呼び出し元だけを新モジュールimportへ変更。golden期待値は不変。
- コミット: `f6178dd`。
- コミット後全テスト: `687 passed`。

### `_evaluate_candidate_134_signal`

- ネスト定義を本体そのままで `src/strategies/signals.py` に移動。
- `src/web/app.py` は同名関数をモジュールimportし、既存の全呼び出し引数は変更なし。
- 閉包依存: なし。
- Phase Aテストは呼び出し元だけを新モジュールimportへ変更。候補1/3/4の重複golden期待値は不変。
- コミット: `b720d41`。
- コミット後全テスト: `687 passed`。

### `_pick_best_market_signal`

- ネスト定義を内部の採用集合・選択順・metadata統合処理ごと `src/strategies/signals.py` に移動。
- 閉包依存 `ACCIDENT_DENT_STRATEGIES` は同名のキーワード専用引数（既定値は空tuple）にした。
- `src/web/app.py` の旧定義位置で `partial(..., ACCIDENT_DENT_STRATEGIES=ACCIDENT_DENT_STRATEGIES)` として従来値を束縛。危険地帯内の既存呼び出し引数・順序は変更なし。
- Phase Aテストは新モジュール関数へ同じ依存値を明示注入。golden期待値は不変。
- 既存のcourse-fit source-location assertionは、採用キー集合の新配置に合わせて `app.py` と `signals.py` の合算を検査するよう変更。venue-mapのapp固有assertionは不変。
