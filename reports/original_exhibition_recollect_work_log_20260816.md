# 独自展示 欠損再取得・会場別対応 作業ログ

作業日: 2026-08-16
対象: `race_original_exhibitions` の `lap_time` / `turn_time` / `straight_time`
状態: 実装・テスト完了。実バックフィルは未実行（発注者承認待ち）。pushなし。

## 実装した能力マップ

| 会場 | 期待フィールド | 扱い |
|---|---|---|
| 1 桐生 | turn, straight | lapは提供なしとして欠損対象外 |
| 5 多摩川 | lap, turn, straight | 3項目を期待 |
| 6 浜名湖 | lap, turn, straight | 3項目を期待 |
| 10 三国 | lap, turn, straight | 3項目を期待 |
| 11 びわこ | lap, turn, straight | 3項目を期待 |
| 13 尼崎 | lap, turn | straightは提供なしとして欠損対象外 |
| 17 宮島 | lap, turn, straight | 3項目を期待 |
| 18 徳山 | lap, turn | straightは提供なしとして欠損対象外 |
| 19 下関 | lap, turn, straight | 3項目を期待 |
| 22 福岡 | lap, turn, straight | 3項目を期待 |
| 24 大村 | lap, turn, straight | 3項目を期待 |
| 3 江戸川 / 9 津 / 16 児島 | なし | 調査結果に基づき非対応化 |

`src/collectors/original_exhibition.py` に `VENUE_FIELD_CAPABILITIES`、
`expected_fields()`、`supported_stadiums()`、`has_complete_expected_fields()` を追加した。
収集ソースがあり、かつ期待フィールドが1項目以上ある会場だけを対応会場とする。

## 3 / 9 / 16 の最小ライブ調査

調査条件:

- 既存の `src.collectors._http.fetch_html` を使用
- `config.USER_AGENT` をそのまま使用
- `config.REQUEST_INTERVAL_SECONDS=2.0` の既存逐次リミッタを使用
- 並列なし
- `config.LAYER3_MAX_RETRIES=1` を調査プロセス内だけに設定
- 各会場1リクエストのみ（合計3リクエスト）
- 本番DBの共有リミッタへ接続しないよう、調査プロセスだけ
  `BOATRACE_SHARED_RATE_LIMIT=0` とし、プロセス内2秒リミッタを使用

| 会場・対象 | 現行候補URLの結果 | 判定 |
|---|---|---|
| 3 江戸川・2026-08-15 12R | 12,482 bytes。titleは「ボートレース江戸川 - 攻略データ」。一周/回り足/直線ラベルなし、パース0行 | 独自展示ページではなく攻略データ。パーサの軽微修正対象ではない |
| 9 津・2026-08-16 12R | 35,438 bytes。titleは「ボートレース津オフィシャルサイト｜レース展望・出場予定選手」。3項目ラベルなし、パース0行 | 指定した当日レース独自展示ではなくレース展望ページ。パーサの軽微修正対象ではない |
| 16 児島・2026-08-13 12R | `hj.kojima-yosou.com` が接続拒否 (`WinError 10061`)。再試行なし | 専用ホスト不通。深追いせず非対応 |

最初のサンドボックス内試行はWindowsのローカル通信拒否で3件とも会場へ到達しなかった。
上表は承認済み外部通信で同じ3件だけを再実行した結果である。追加URL・追加レースの
調査は行っていない。

結論として3/9/16の `SOURCE_PATTERNS` は空にし、能力も空集合にした。これにより通常収集、
欠損検索、バックフィルCLIのどこからもHTTP対象にならない。新しい公式提供URLが別途確認
できるまで無駄打ちを停止する。

## 欠損判定の修正

`src/db/cron_runtime.py::find_missing_original_exhibition_races` は、従来の
「全会場で6艇×3項目」をやめ、会場の期待フィールドだけが6艇そろっているかを共通ヘルパーで
判定する。

- 桐生: lapが全艇NULLでも、turn/straightが6艇そろえば完了
- 尼崎・徳山: straightが全艇NULLでも、lap/turnが6艇そろえば完了
- 3項目提供会場: 期待項目の1艇分でも欠ければ本当の欠損としてdue
- 3/9/16および未検証会場: supported集合に入らず、欠損検索・収集対象外

コレクタ内部の `_filter_missing` も同じ共通判定を使うため、cronで選んだ本当の欠損が
コレクタ側の異なる基準で落ちることがない。

## 再取得経路

追加: `scripts/backfill_original_exhibition.py`

安全な確認例（HTTP・DB書き込みなし）:

```powershell
.venv\Scripts\python.exe scripts\backfill_original_exhibition.py `
  --date 2026-08-16 --stadiums 5 13 --limit 12
```

発注者承認後だけ `--execute` を付ける。実行時も既存の
`original_exhibition.collect_for_races()` を1回だけ呼び、内部の `_http` を通る。

安全策:

- 既定はplan-only。`--execute` がなければHTTPもDB書き込みも行わない
- 対象は「検証済み提供会場 × 能力マップ上の本当の欠損」の積集合のみ
- `--limit` 既定12、最大120
- 1レースあたりURL候補は `--pattern-limit` 既定1、最大2
- 並列処理なし。`config.REQUEST_INTERVAL_SECONDS` が2.0秒未満なら起動拒否
- `force=False` 固定で、実行直前にもコレクタ側で欠損を再確認
- 取得不能でも外側の再試行ループなし。1回の実行結果を表示して終了
- 3/9/16を明示指定すると引数エラーで拒否

この作業ではplanモードを含めCLIを実DBへ接続しておらず、実バックフィルは実行していない。

## テスト結果

- focused（collector/runtime/CLI）: `31 passed`
- expanded（独自展示fixture、scheduler、source regressionを含む）: `83 passed`
- 指定全体:
  `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - `951 passed, 1 skipped, 1 warning in 26.57s`
  - Windows共有TEMPの既知ACL問題を避けるため、プロセスローカルの `TEMP` / `TMP` のみを
    リポジトリ内の記録済みpytest一時ディレクトリへ変更
  - warningは既存 `.pytest_cache` ACL warning
- Python compile: pass
- `git diff --check`: pass

追加回帰は、尼崎/徳山のstraight欠損、桐生のlap欠損がdueにならず、多摩川の期待turnが
1艇欠けたケースだけがdueになることをfake SQLite DBで確認する。CLIについてはplan-only、
明示execute、limit、pattern-limit、3/9/16拒否をネットワークなしで確認する。

## 変更禁止領域・運用状態

- ROI、予測、DBスキーマ、`render.yaml` は変更なし
- `config.REQUEST_INTERVAL_SECONDS`、`config.USER_AGENT` は変更なし
- 収集対象会場の拡大なし（3/9/16は縮小・停止）
- ローカルscheduler、server、production writerは起動していない
- push、deployなし

## ローカルコミット

- `572a905` `Make original exhibition gaps venue-aware`
- `c722878` `Add bounded original exhibition recollection CLI`
- 本作業ログは別の最終ローカルコミットに収録する
