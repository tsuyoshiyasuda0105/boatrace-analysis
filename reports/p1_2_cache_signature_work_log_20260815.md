# P1-2 キャッシュ署名コード由来化 作業ログ

作業日: 2026-08-15  
対象: `src/roi_contract.py::strategy_definition_signature()`  
状態: 実装・検証完了（ローカルコミットのみ、push / deploy なし）

## 変更内容

- `adopted_strategies.md` の SHA-1 を署名根拠にする実装を廃止した。
- 固定順の戦略ソース5ファイルと、キャッシュバージョン定数3値を、ラベル・バイト長付きで SHA-1 に投入し、従来互換の先頭10桁hexを返すようにした。
- 読み取れない戦略ファイルは例外にせずスキップする。全ファイル欠損時もバージョン定数と署名形式値から決定的な10桁hexを返し、`nosig` へ誤って縮退しない。
- 計算本体を `lru_cache` でプロセス内メモ化した。公開関数の引数と戻り値形式、および Web / scheduler / prewarm / backfill の呼び出し側は変更していない。

## ハッシュ対象

1. `src/strategies/signals.py`
2. `src/evaluation/l4_strategy.py`
3. `src/evaluation/course_fit_strategy.py`
4. `src/evaluation/accident_dent_strategy.py`
5. `src/evaluation/omura_124_original_strategy.py`
6. `ROI_DAILY_CACHE_VERSION` の名前と値
7. `MARKET_SIGNALS_CACHE_VERSION` の名前と値
8. `STRATEGY_PAGE_CACHE_VERSION` の名前と値

入力順は上記で固定し、各入力には種類と相対パスまたは定数名を含める。`adopted_strategies.md` は対象外。

## 追加テスト

`tests/test_roi_contract.py` に10ケースを追加した。

- 5つの対象ソースをそれぞれ単独で変更すると署名が変わること。
- 3つのバージョン定数をそれぞれ単独で変更すると署名が変わること。
- `adopted_strategies.md` の内容差では署名が変わらないこと。
- 対象ソースがすべて欠損しても、例外や `nosig` ではなく決定的な10桁hexになること。

## 検証結果

- 編集前全体: `687 passed, 1 warning`
- 専用: `13 passed, 1 warning`（既存3ケース + 新規10ケース）
- 編集後全体: `697 passed, 1 warning`
- 別々に起動した2プロセスの署名: いずれも `37ba2789bd`
- `git diff --check`: pass
- warning は既存の `.pytest_cache` 書き込み警告のみで、収集・assertion への影響なし。

## スコープ確認

- 戦略ロジック、ROI値、予測、DBスキーマ・データ、`render.yaml`、cronスケジュール・呼び出し側は変更していない。
- ローカル scheduler、サーバー、ブラウザ、production writer は起動していない。
- push / deploy は実施していない。

## デプロイ時の一過性挙動

署名値が旧方式から新方式へ切り替わるため、デプロイ直後は署名を含む既存キャッシュが一斉に不一致になる。初回アクセスまたは prewarm で新キーの再計算が走る一過性コストが発生するが、self-heal / prewarm が収束すれば解消する。デプロイ確認では新署名が `nosig` でないことを確認する。
