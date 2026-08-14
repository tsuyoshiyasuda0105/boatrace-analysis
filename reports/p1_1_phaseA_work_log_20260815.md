# P1-1 フェーズA 作業ログ（2026-08-15）

## 作業スコープ

- 作業: 戦略ロジックの棚卸しと、現挙動を固定する特性化テストの追加。
- 使用スキル: `project-ops-guard`。
- 追加予定ファイル: `reports/strategy_inventory_20260815.md`、`tests/test_strategy_characterization_phase_a.py`、本ログ。
- 変更禁止: `src/web/app.py`、既存モジュール、`src/strategies/`、ROI・予測・DB スキーマ、`render.yaml`。
- 競合回避: 既存の未追跡 `.serena/` と `reports/` 内の無関係ファイルには触れず、コミット時は対象パスだけを明示して stage する。
- 実行プロセス: 有限のコード検索、pytest、Git コマンドのみ。ローカルサーバー、scheduler、browser、本番 writer、バックグラウンドプロセスは起動しない。
- push: 禁止。ローカル `main` へのコミットだけを行う。

## 事前確認・失敗ログ

- 作業開始時点は `main...origin/main [ahead 9]`。`src/web/app.py` と `render.yaml` に未コミット差分なし。
- 最初の指定どおりの全件実行 `.venv/Scripts/python.exe -m pytest tests/ -q` は 670 件を収集し、627 passed / 43 setup errors。すべて `C:\Users\tsuyo\AppData\Local\Temp\pytest-of-tsuyo` の `PermissionError: [WinError 5]` で、テスト assertion や製品コードの失敗ではない。
- 原因: pytest の共有 OS 一時ディレクトリがこの実行環境から読み書きできない。
- 再発防止: 以降の全件・対象テストはリポジトリ内の専用 `--basetemp .pytest_tmp_p1_phasea_20260815` を使用し、同じ670件以上が green であることを確認する。
- 作業後の削除対象: `C:\boat_project\boatrace-analysis\.pytest_tmp_p1_phasea_20260815` のみ。正規化した絶対パスがリポジトリ直下のこの名前と一致することを確認してから削除する。ソース、テスト、レポート、DB、キャッシュ、本番データは削除対象ではない。

## 棚卸し結果

- ASTによる明示基準で、戦略評価関連関数は61。内訳はトップレベル4、`create_app`直下5、`market_signals_for_date`内52。
- 61関数すべてを `reports/strategy_inventory_20260815.md` の表に記録し、役割、主入力、重複経路、今回のpin状態を付与した。
- 主な重複:
  - L4本流は `market_signals_for_date`、`_l4_daily_stats`、`_l4_races_for_date`、`scripts/sync_l4_summary_to_supabase.py`、`scripts/send_l4_alerts.py`、`result_scraper.py` に分散。
  - B除外8会場は app.py 内だけでも3表現、さらにshared module・sync・scraper・odds schedulerに再記述。
  - G2/G3 optB と general C は live evaluator と `_l4_daily_stats` に条件を二重実装。
  - 1c80/L4 PRO は app側がshared moduleを使う一方、sync cron内に別helperがある。
  - 2号壁は定義tupleを共有するが、live/daily判定関数は別実装。
- CLAUDE.mdが示す `src/notifications/send_l4_alerts.py` は存在せず、実体は `scripts/send_l4_alerts.py`。

## 追加した特性化テスト

- `tests/test_strategy_characterization_phase_a.py` を追加。app.pyのネスト関数を変更せず、ASTから対象定義だけをtest namespaceへ読み込む足場を使用。外部ネットワーク・本番DB・ローカルDB接続なし。
- 17 pytest caseで9関数の現出力を直接pin:
  - G2/G3 optBの完全dictと、会場外・1000円上限・motor cap超過・雨・女性の除外境界。
  - candidate 1/3/4同時成立時のcand4優先とmatched一覧。
  - general Cの完全dictと、B除外・雨・女性gate。
  - 採用key優先のbest signal選択とmatched metadata。
  - 女性混入時のgeneric拒否、ニッチ/ROI key/明示許可。
  - general200 overlay時の採用label優先とmetadata保持。
  - 鉄板度の5★圧縮、retired general200のNone、艇5/A2/tilt3.0ニッチの完全dict。
- pin不能の危険地帯はinventoryへ赤字で記録: endpoint全体、日別/日別レース集計、旧市場非効率、津/住之江・下関履歴、潮位・展示・決まり手・current motor・1-3系列・非展示core・2号壁。

## 検証結果

- 作業前基準（専用basetemp）: `670 passed, 1 warning`。
- 追加テスト単体: `17 passed, 1 warning`。
- 最終全件: `687 passed, 1 warning`。基準670を17件上回り、失敗・errorなし。
- warningは作業前からある `.pytest_cache` nodeids pathの書込警告のみで、collection/assertionへ影響なし。
- `reports/strategy_inventory_20260815.md` の評価関数表は61行でAST件数と一致。
- `src/web/app.py`、既存module、`src/strategies/`、ROI/予測/DB schema、`render.yaml` の差分なし。追加範囲は `tests/` と `reports/` のみ。
- ROI ledger、予測、本番DB、schema、cron、render設定には触れていないため、本番データ整合性checkは非該当。
- push、deploy、local scheduler、server、browser、production writerは実行していない。running processなし。
- 事前登録した `C:\boat_project\boatrace-analysis\.pytest_tmp_p1_phasea_20260815` は、正規化絶対パスが期待値かつrepository配下であることを確認後に削除し、現在は存在しない。他のfile/dataは削除していない。
- test commit前の `git diff --cached --check` はテストファイル末尾の余分な空行を報告したが、PowerShellが後続commitを継続した。原因は末尾改行の目視不足。予防として末尾を修正し、最終scope commit前に全対象への`git diff --check`を再実行する。

## コミット

- `95022de` — `Inventory current strategy evaluation logic`（棚卸しレポートのみ）
- `b0740ef` — `Pin current strategy evaluator behavior`（特性化テストのみ）
- 本ログと末尾空行修正は最終scope commitに収録する。正確な最終HEADは作業完了出力で報告する。

## 完了時チェック

- [x] repository statusと競合を確認し、無関係な未追跡ファイルを保持。
- [x] 現挙動と重複箇所をコードから特定してからテストを追加。
- [x] 最小scope（新規tests/reportsのみ）を維持。
- [x] 対象テストと全テストを実行。
- [x] 670件を割らず、最終687件green。
- [x] result ingestion、事故snapshot、ROI ledgerは変更がなく非該当。
- [x] local scheduler・production writer・serverを起動していない。
- [x] 一時pytest tool以外のprocessなし。専用一時directoryは事前記録した対象だけを検証後に削除する。
