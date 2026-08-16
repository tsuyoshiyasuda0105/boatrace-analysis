# 勝ち筋サーチ Step 14 実行結果 — 公式競走成績ファイル不足分取得CLI

実施日: 2026-08-16

## 作成ファイル

- `scripts/fetch_missing_results.py`: Kファイルの不足走査・逐次取得CLI
- `tests/test_fetch_missing_results.py`: `download_lzh` をモックしたネットワーク不使用テスト
- `docs/kachisuji_result_files_step14_result_20260816.md`: 本レポート

`src/collectors/official_dl.py`、`src/collectors/_http.py`、`config.py`、DB、既存の取得データは変更していない。

## CLI仕様

```powershell
python scripts/fetch_missing_results.py --scan
python scripts/fetch_missing_results.py --scan --from 2025-07-01 --to 2026-08-16
python scripts/fetch_missing_results.py --from 2025-07-01 --to 2025-12-31
python scripts/fetch_missing_results.py --from 2025-07-01 --to 2026-08-16 --limit 50
python scripts/fetch_missing_results.py --from 2025-07-01 --to 2026-08-16 --limit 50 --log-file logs/fetch-results.log
```

- `--scan`: ローカルの `data/raw/results/` のみを走査する。取得関数は呼ばない。期間既定値は 2016-06-01 から実行日まで。
- `--from` / `--to`: 包含期間。取得モードでは両方必須。
- `--limit N`: その実行で処理する不足日を日付昇順の先頭 N 件に制限する。
- `--log-file`: コンソールと同じログを UTF-8 で保存する。
- ファイルの存在判定は大文字小文字を区別せず、対象日の `K*.TXT` または `k*.lzh` のどちらか一方があれば取得済みとする。
- 取得モードは不足日のみを昇順に1本のループで処理する。各日を `ok`、`skip_existing`、`not_found`、`error` に分類し、10件ごとに処理数・残件数・推定残り秒数を表示する。最後に分類別および取得不可理由別の件数を表示する。
- 各日について既存ファイルを直前に再確認し、存在すれば `download_lzh` を呼ばない。`force=True` は使用しないため、既存ファイルを上書きしない。
- CLI独自の再試行は行わず、1日につき `download_lzh` 呼出しは最大1回である。失敗日があっても次の日へ進む。

## `--scan` 結果

実行コマンド（ローカル走査のみ、HTTPなし）:

```powershell
.venv\Scripts\python.exe scripts\fetch_missing_results.py --scan --from 2025-07-01 --to 2026-08-16
```

不足日数: **325日**

| 月 | 不足日数 |
|---|---:|
| 2025-07 | 31 |
| 2025-08 | 31 |
| 2025-09 | 30 |
| 2025-10 | 31 |
| 2025-11 | 30 |
| 2025-12 | 31 |
| 2026-01 | 31 |
| 2026-02 | 28 |
| 2026-03 | 31 |
| 2026-04 | 30 |
| 2026-07 | 7 |
| 2026-08 | 14 |
| **合計** | **325** |

2026-05 と 2026-06 の不足は0日だった。Codexは取得モードを実行しておらず、HTTPリクエストは発生していない。

## レート制限・スクレイピング規約を守る実装

- CLIは `scripts/fetch_missing_results.py` の単一 `for` ループから、既存の `official_dl.download_lzh("K", target_date)` だけを逐次呼び出す。並列化機構や直接HTTPクライアントは追加していない。
- `src/collectors/official_dl.py::download_lzh` はHTTP直前に `_wait_interval(_rate_limit_host(url))` を必ず呼ぶ。
- `_wait_interval` は `config.REQUEST_INTERVAL_SECONDS` を使用し、ローカルリミッタでは不足分を `time.sleep` する。既存値は `2.0` 秒のまま変更していない。
- HTTPの User-Agent は既存の `config.USER_AGENT` をそのまま使う。CLIから変更・上書きしない。
- CLIはKファイルだけを指定し、Bファイル、DB、スケジューラ、デプロイ処理には接続しない。

## テスト結果

- 新規テスト: `7 passed`
- 新規テスト + 関連既存テスト（`test_official_dl.py`、`test_shared_rate_limit.py`）: `20 passed`
- Pythonコンパイル: 成功

全新規テストは autouse fixture で `official_dl.download_lzh` をモックし、ネットワークを使用していない。TXT/LZH存在判定、既存日の呼出し禁止、`not_found` 後の継続・集計、`--limit`、`--scan` の無通信、日付昇順、例外後の継続と理由別集計を検証した。

## 既知の制限

- 開催がない日や公式側にアーカイブがない日は取得できない。この場合も停止せず `not_found` として記録する。
- 既存 `download_lzh` の公開契約は、404、通信例外、200以外の応答をすべて `None` にまとめる。このためCLIは `None` を `not_found`（理由 `download_lzh_returned_none`）として集計し、呼出し自体が例外を送出した場合だけ `error` とする。レート制限を迂回してHTTP応答を再判定・再試行することはしない。
- 取得するのはLZHファイルだけで、解凍、解析、事故履歴復元、検索DB再生成は行わない。
- 実取得は通信量と所要時間を管理するリンが行う。325件すべてがHTTP対象なら、2秒間隔だけでも最低約11分を要するのが正常である。
