# 勝ち筋サーチ Step 17 実装結果 — 選手名検索

実施日: 2026-08-16

## 結果

艇別条件の選手欄で、漢字の姓・名・フルネーム、および正規化したカナの部分一致から候補を選べるようにした。候補を確定すると条件JSONには従来どおり数値の `racer_id` が入り、検索結果の条件サマリには `選手: 峰竜太(4320)` のように名前を表示する。選手番号の直接入力も維持している。`src/search/roi_search.py` は既存の `racer_id` 検索をそのまま利用し、変更していない。

## 作成・変更ファイル

- `scripts/sync_kachisuji_racers.py`: 選手マスタの独立同期CLI
- `src/kachisuji_web/app.py`: 読み取り専用の選手検索APIと検索文字正規化
- `src/kachisuji_web/templates/search.html`: 6艇分のオートコンプリート、数値payload、名前付き条件サマリ
- `src/kachisuji_web/static/kachisuji.css`: 候補リストの表示スタイル
- `tests/test_sync_kachisuji_racers.py`: 同期処理と書込み範囲のテスト
- `tests/test_kachisuji_web.py`: API、正規化、limit、特殊文字、ARIAのテスト
- `tests/e2e/test_kachisuji_e2e.py`: キーボード・クリック・Esc・直接番号・payload・サマリのE2E
- `docs/kachisuji_racer_search_step17_result_20260816.md`: 本レポート

本番 `src/web/`、`src/search/roi_search.py`、`data/boatrace.db` は変更していない。

## マスタ複製方法と実行結果

次のCLIを追加し、今回のローカル `data/kachisuji_search.db` に実行した。

```powershell
.\.venv\Scripts\python.exe scripts\sync_kachisuji_racers.py
```

CLIは `data/boatrace.db` を SQLite URI `mode=ro` と `PRAGMA query_only=ON` で開き、`racer_number, name, name_kana` を読み取る。出力側で仕様どおりの `racers` テーブルを `CREATE TABLE IF NOT EXISTS` し、このテーブルだけをスナップショットで置換する。ソースと出力が同一パスの場合は拒否する。実行結果は1,643件で、ソースと出力の選手マスタ論理SHA-256はいずれも `03caf631e4d7c14c488f80ae2eb828aad919995f8ebd943d8930e020ef646ee4` だった。

実行前後で `data/boatrace.db` のサイズ、mtime、racers件数、論理ハッシュは不変だった。検索DBの既存テーブルも `asof_race_features=557,617`、`accident_events=49,189`、`odds_snapshot=13,030,739`、`racer_starts=3,351,378`、`start_timing_events=3,362,034` で不変だった。書込みは検索DBの新規 `racers` テーブルだけである。全期間の特徴量再生成は不要。

このチェックアウトではCodexが複製済みなので、リンが追加でマスタ複製を実行する必要はない。別の検索DB、新しい環境、または半期マスタ更新後には、同じCLIをその環境で1回実行する必要がある。半期更新の自動運用は今回の範囲外で、将来課題とする。

## API仕様

`GET /api/racers?q=峰&limit=15`

- 応答: `[{"racer_number":4320,"name":"峰竜太","name_kana":"ﾐﾈ ﾘｭｳﾀ"}, ...]`
- `name` と `name_kana` の正規化後文字列を部分一致検索する。
- `limit` は既定15、最小1、最大50。整数でない値は既定15に戻す。
- SQLの検索文字、prefix判定、limitはすべてパラメータバインドする。`\`、`%`、`_` はLIKEパターン上でリテラルとしてエスケープする。
- 検索DBは `mode=ro` と `PRAGMA query_only=ON` で開く。
- 空白だけ、1文字のASCII・カナ・記号は空配列。仕様内の主要例 `q=峰` と「2文字未満は空配列」は同時に満たせないため、1文字のCJK漢字だけを明示的な例外として許可した。これにより `q=峰` は峰竜太、峰重侑治、峰重力也、赤峰和也、菊池峰晴、木田峰由季の6件を返す。

## カナ検索の対応範囲

PythonのUnicode NFKC正規化後、ひらがなをカタカナへ変換し、半角・全角空白を除去して比較する。このため、保存値 `ﾐﾈ ﾘｭｳﾀ` に対して `みね`、`ミネ`、`ﾐﾈ`、`りゅうた`、`リュウタ`、空白を除いたフルネームカナが部分一致する。NFKCにより半角濁点付きカナも通常の全角合成文字へ統一される。

既知の制限として、濁点そのものを無視する検索ではないため、例えば濁音の保存値を清音だけで入力する曖昧一致は行わない。長音・異体字・読みの別表記・ローマ字も展開しない。候補は現時点の1,643人スナップショットに限られる。

## UI操作

- 各艇の「選手」へ名前またはカナを入力すると、250msのデバウンス後に候補を名前・番号・カナ付きで表示する。
- 候補をクリック、または上下矢印で移動してEnterで確定する。Escと欄外クリックで閉じる。
- 入力は `role="combobox"`、候補は `role="listbox"` / `role="option"`。`aria-expanded`、`aria-controls`、`aria-activedescendant`、`aria-selected` を状態に合わせて更新する。
- 選択後の表示は `峰竜太 (4320)`、内部条件は数値 `4320`。Step 16の条件サマリは `選手: 峰竜太(4320)` と表示する。
- `4320` または従来形式の `4320 峰竜太` を直接入力した場合も、数値 `racer_id` として検索する。

## テスト結果

- focused unit/API/sync: 40 passed
- focused Step 17 E2E: 3 passed
- 全非E2E: 997 passed, 1 skipped
- 全メインE2E（ポート8090）: 67 passed
- Round 3 E2E（ポート8090へ明示上書き）: 3 passed
- Python構文確認、`git diff --check`: passed

pytestの既存キャッシュACLに関する `PytestCacheWarning` は継続しているが、テスト結果には影響しなかった。E2E fixtureは各実行後にFlask/Chromiumを終了する。ネットワーク、スケジューラ、デプロイ、pushは実行していない。
