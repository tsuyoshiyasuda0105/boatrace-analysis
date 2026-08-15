# 勝ち筋サーチ Step 8 実装結果

実施日: 2026-08-16

## 実装結果

- オッズ条件の `snapshot` は `T-5min` だけを許可する。
- `snapshot` 省略時は `T-5min` を使用する。
- `final` またはその他の値は「オッズ条件は5分前オッズ(T-5min)のみ対応しています」という日本語エラーで拒否する。Web APIの検索・保存はいずれもHTTP 400を返す。
- min/maxの包含境界、オッズ行なしの `condition_null` 除外、当日照合のpending分類、3連単限定、非3連単の日本語エラーは維持した。

## 保存済みfinal手法の扱い

既存の保存済み手法に `snapshot='final'` が含まれていても、保存データを自動変換・削除しない。一覧表示には残すが、再照合時はHTTP 400と上記の日本語案内を返す。

過去に保存した条件を暗黙に別の時点へ変換すると手法の意味が変わるため、`T-5min` への自動フォールバックは採用しなかった。利用者はT-5条件で新しい手法を検索・保存し直す。

## UI変更

- final/T-5minの時点選択UIを削除し、オッズ条件を「3連単オッズ（5分前・T-5）」に固定した。
- バッジを `⏱` と `📅 2024/6〜` に統一した。
- 注意書きを「5分前オッズで絞り込みます。締切前に分かる値なので当日照合でも使えます。データは2024年6月以降です。」へ差し替えた。
- ブラウザから送るオッズ条件は常に `snapshot: 'T-5min'` となる。

## 変更ファイル

- `src/search/roi_search.py`
- `src/kachisuji_web/templates/search.html`
- `tests/test_roi_search.py`
- `tests/test_strategies.py`
- `tests/test_kachisuji_web.py`
- `tests/e2e/test_kachisuji_e2e.py`
- `docs/kachisuji_odds_t5only_step8_result_20260816.md`

`src/search/strategies.py` は、オッズを既に当日確定条件としてpending分類しており、final前提の記述もなかったため変更していない。`src/kachisuji_web/app.py` は既存の日本語バリデーション伝播でHTTP 400になるため変更していない。CSS変更も不要だった。

## テスト結果

- focused unit/API: 102 passed
- Kachisuji unit/contract bundle: 149 passed
- 通常E2E: 53 passed（Step 8追加1件を含む）
- Step 8 DoD対象: 202 passed（unit/contract 149 + E2E 53）
- E2E終了後のport 8090 listener: 0

参考としてリポジトリ全体の `pytest tests/ -q` も実行した。937件中932件が成功し、Step 8変更外の既知問題で2 failures / 3 setup errorsだった。setup errorsは2つのPlaywright suiteを同じpytestプロセスで続けた場合のSync API/async loop競合で、通常E2Eを単独実行すると53/53成功した。残る既知failureは共有PKマップの `odds_snapshot` 未登録、graceful degradationのHTTP 400、Round 3実データ照合のmatched/search 4/3差分であり、いずれもStep 8仕様の変更許可外である。

## データと既知の制限

- `odds_snapshot` に既存のfinal行が残っていても削除しない。検索コンパイラがfinalを拒否するため検索経路からは利用できない。
- オッズ条件は3連単だけに対応する。単勝・2連単オッズは未収集である。
- T-5minデータの表示上の対象期間は2024年6月以降であり、実際に行がないレースは従来どおり `condition_null` または当日pendingとなる。
- `data/boatrace.db` には接続していない。`data/kachisuji_search.db` への書き込みも行っていない。
