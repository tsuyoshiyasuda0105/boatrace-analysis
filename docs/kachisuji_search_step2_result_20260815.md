# 勝ち筋サーチ Step 2 実装結果（2026-08-15）

## 作成ファイル

- `src/search/__init__.py`
- `src/search/roi_search.py`
- `scripts/search_roi.py`
- `tests/test_roi_search.py`
- `docs/kachisuji_search_step2_result_20260815.md`（本レポート）

既存ファイルは変更していない。開発・テストでは pytest の一時ディレクトリ内に作成した合成 SQLite DB のみを使用し、`data/kachisuji_search.db` と `data/boatrace.db` には接続していない。ネットワーク、スケジューラ、Web、デプロイ、push も使用していない。

## テスト結果

- 指定テスト: `.venv/Scripts/python.exe -m pytest tests/test_roi_search.py -q --basetemp .pytest_tmp_kachisuji_step2_20260815`
- 結果: **33 passed**（1.23 秒）
- 静的検査: `.venv/Scripts/ruff.exe check src/search/__init__.py src/search/roi_search.py scripts/search_roi.py tests/test_roi_search.py` → **All checks passed**
- 構文検査: 対象 Python 4 ファイルを `compile(..., "exec")` → **ok**
- pytest の警告1件は、既存の `.pytest_cache` に nodeids を作成できない Windows 環境警告。製品コードおよびテスト結果への影響はない。

テストでは、単勝・2連単・3連単の完全一致と手計算ROI、参照列だけに適用されるNULL除外、全 boats 演算子、`faster_by` / `slower_by`、小N閾値、seed固定ブートストラップ、点推定を含むCI、年別集計、空結果、履歴条件の開始日制限、未知キー拒否、読み取り専用URI、CLIのUTF-8 JSONを確認した。

初回CLIテストは 29/30 件成功だったが、Windows の標準出力が cp932 となりUTF-8デコードに失敗した。CLI起動時に stdout/stderr を UTF-8 へ明示設定して修正し、再発防止テストを残した。末尾空白の `rg` 検査は一致なしを表す通常の exit 1 だった。

## 条件からSQLへの変換設計

- トップレベル、boat番号、boat条件、範囲演算子、決まり手名、bet種別を固定ホワイトリストで検証する。未知キーは `ValueError` とし、利用者入力を列名や演算子へ直接展開しない。
- 値はすべて `?` プレースホルダーでパラメータバインドする。SQLへ組み込む識別子は内部ホワイトリストからのみ生成する。
- `asof_race_features` に対する結合なしの単一SELECTで、`schema_version=2`、日付、会場、環境、艇別条件を構成する。
- 条件列ごとに `(条件成立 OR 列 IS NULL)` をWHEREへ入れる。別条件に明確に不成立の行はSQLで落とし、残った行の `CASE WHEN 条件列 IS NULL` を Python 側で `excluded.condition_null` に分類する。条件が参照しない列のNULLは判定に使わない。
- bet種別に応じて `result_tansho` / `result_nirentan` / `result_sanrentan` と対応する `payout_*` だけを選択する。結果または払戻がNULLなら `excluded.result_missing`、指定組との完全一致だけを的中とする。
- Step 1 の `bN_ex_dev = 展示タイム - 6艇平均` に合わせ、`faster_by: x` は `bN_ex_dev <= -x`、`slower_by: x` は `bN_ex_dev >= x` に変換する。
- 決まり手・事故率の有効条件がある場合は検索開始日を `2023-05-01` 以降へ切り上げる。空の範囲オブジェクトやnullは指定なしとして扱い、切り上げない。
- ブートストラップは払戻値（外れは0）の経験分布を `numpy.random.Generator.multinomial` で1000回再標本化する。これは行単位の復元抽出と同値で、同一払戻値を集約して大規模データ時の計算量とメモリを抑える。デフォルトseedは42。`--fast` は標本平均の正規近似95%CIを使う。

## payout 単位の確認

Step 1 の `src/features/asof_builder.py` は `race_payouts.payout` を整数のまま `payout_tansho` / `payout_nirentan` / `payout_sanrentan` に保存する。これは100円投票に対する払戻金（円）である。したがって、各対象レースで指定1点を100円購入した場合のROI（%）は、的中払戻の合計を `N × 100` で割って100倍した値、すなわち外れを0とした払戻値の平均になる。

## 既知の制限・確定動作

- 実データ55万行での性能測定とデータ内容の照合は、バックフィル完了後に発注者が行う。本実装では仕様どおり合成DBだけでロジックを検証した。
- `effective_date_range` は条件・結果の両方が評価可能で母数に入ったレースの日付範囲であり、`n=0` では `[null, null]` を返す。
- 全キー省略可能という仕様の下でROI対象を一意にするため、`bet` が省略またはnullの場合は仕様例の3連単 `1-2-3` を既定買い目とする。部分的な買い目、艇番重複、範囲外艇番はエラーにする。
- 正規近似CIの下限は、払戻ROIが負にならないため0に丸める。ブートストラップCIは点推定を必ず含むよう端点を保守的に広げる。
