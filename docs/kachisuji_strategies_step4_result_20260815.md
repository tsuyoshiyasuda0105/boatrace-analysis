# 勝ち筋サーチ Step 4 実装結果

実施日: 2026-08-15

## 作成・変更ファイル

新規作成:

- `src/search/strategies.py`: 手法DBスキーマ、保存・一覧・取得・無効化、指定日照合
- `scripts/match_strategies.py`: 全有効手法または指定IDを照合するCLI
- `tests/test_strategies.py`: ライフサイクル、条件検証、confirmed/pending、API、CLI、読み取り専用接続のテスト
- `docs/kachisuji_strategies_step4_result_20260815.md`: 本レポート

変更:

- `src/kachisuji_web/app.py`: 手法API 5系統と手法DB設定を追加
- `src/kachisuji_web/templates/search.html`: 保存、手法一覧、削除、日付別照合UIを追加

上記以外の既存ファイルは変更していない。`src/search/roi_search.py`、`src/web/`、
`data/kachisuji_search.db`、`data/boatrace.db` は変更していない。

## テスト結果

- `.venv/Scripts/python.exe -m pytest tests/test_strategies.py tests/test_kachisuji_web.py tests/test_roi_search.py -q -p no:cacheprovider --basetemp .pytest_tmp_kachisuji_step4_20260815`
  - 52 passed
- Pythonコンパイル: `src/search/strategies.py`、`src/kachisuji_web/app.py`、
  `scripts/match_strategies.py`、`tests/test_strategies.py` 成功
- `search.html` 内JavaScriptの構文検査: 成功
- Web APIの検証は Flask `app.test_client()` のみ。Flaskサーバーは起動していない。

初回ベースライン実行は共有Windows Tempへのアクセス拒否で43件の setup error となったため、
リポジトリ内の専用 `--basetemp` に切り替えた。製品コードの assertion failure ではない。
開発中のCLIテスト1回は、親テストプロセスがUTF-8出力をcp932として読んだため失敗した。
テストに `encoding="utf-8"` を指定し、最終実行では全件成功した。

## Step 2 条件パーサの再利用

`src/search/strategies.py` は `src.search.roi_search._compile_conditions()` を直接呼び、
次の情報を一括して取得している。

- 未知キーや値域を含む条件JSONのバリデーション
- Step 2と同一のSQL WHERE句とバインド値
- 条件が参照する列の一覧
- 買い目の種類と期待値

Step 2には公開された条件コンパイラがなく、公開関数 `search_roi()` はレース行を返さないため、
条件解釈を重複実装せずに済む既存の内部関数を再利用した。SQLの値はすべてパラメータ
バインドし、コンパイラが返した列名も固定形式の識別子として再検証している。

日次照合では保存条件の `date_from` / `date_to` を探索期間として扱い、明示された
`target_date` で置き換える。それ以外の条件解釈はStep 2と同一である。

## pending 判定

Step 2コンパイラが返す参照列を使って、WHERE句に合致した候補行を次の順で分類する。

1. 参照する前日確定列にNULLが1つでもあれば返さない。
2. 前日確定列がすべて合致し、当日確定列だけがNULLなら `pending` に入れる。
3. 参照列がすべて非NULLなら `confirmed` に入れる。

当日確定列は現行Step 2/UIの定義に合わせ、`weather`、`wind_speed`、および各艇の
`ex_rank`、`ex_dev`、`ex_st` とした。NULLの当日列名を `undetermined_columns` に返す。
潮汐 `tide_phase` は天文計算で前日に確定できるため前日列として扱う。

検索DBは `mode=ro` かつ `PRAGMA query_only=ON` で開く。テストでも検索DBへの接続が
読み取り専用URIだけであることを確認した。書込みは独立した手法DBだけに限定する。

## 既知の制限

- 認証・課金・ユーザー管理は未実装で、既定 owner は `local`。
- スケジューラ登録、通知、フォワードROI集計は今回の範囲外。CLIのみ用意した。
- Step 2が将来新しい当日確定条件列を追加した場合、pending用の当日列集合と対応テストも
  同時に更新する必要がある。
- 条件コンパイラはStep 2の非公開関数なので、将来公開API化された場合はimport先を切り替える。
- 手法DBは初回の保存または一覧取得時に自動作成する。SQLiteファイル自体はソース管理しない。

## 制約確認

- 実サーバー起動なし
- `data/kachisuji_search.db` への書込みなし
- `data/boatrace.db` への接続なし
- ネットワーク、スケジューラ、デプロイなし
- pushなし
