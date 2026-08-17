# 市場シグナル夜間更新 OOM 修理 作業ログ（2026-08-18）

## 結論

- `prewarm_strategy_pages.py` は市場シグナル計算中に `predictor` を参照しておらず、永続 `predictions` テーブルを含むDBデータだけで従来の判定を完結していた。一方、従来は通常の `create_app()` を使うため、ローカルでは `Predictor` のimport・構築・モデルloadまで行っていた。
- ローカル144レースの実測では512 MiB超を単独再現しなかったが、従来経路のpeak RSSは314.41 MiBだった。Render cronプロセスの他の常駐量と512 MiB制限を考えると、不要なモデル系メモリがOOM余裕を大きく削る仮説と整合する。
- `create_app(cached_predictions_only=True)` に統一後はpeak RSS 185.32 MiB、従来比 -129.09 MiB（-41.1%）。同じ144レースを17.82秒で完走し、512 MiB内に収まった。
- 8/18の市場シグナル出力は、computed timestampを除いた全payload SHA-256と少数レースの判定をbefore/afterで完全一致確認した。L4判定ロジックは変更していない。

## RSS計測

条件:

- DB: ローカル `data/boatrace.db`（`DATABASE_URL`を空に固定。本番Supabase接続なし）
- 対象日: `2026-08-18`
- race source: 144/144
- 実行: `.venv/Scripts/python.exe scripts/prewarm_strategy_pages.py --mode signals --date 2026-08-18`
- 200ms間隔で、起動した子PIDとその子孫だけのRSS合計を監視

| 経路 | exit | 経過 | peak RSS | signals |
|---|---:|---:|---:|---:|
| before: 通常 `create_app()` | 0 | 40.98秒 | 314.41 MiB | 12 |
| after: cached predictions only | 0 | 17.82秒 | 185.32 MiB | 12 |
| 差 | - | -23.16秒 | -129.09 MiB | 0 |

診断上の注意:

- ローカル単独では512 MiB超/OOM killそのものは再現しなかったため、「OOMを直接再現した」とは結論しない。
- 本番観測の254.7秒、3回連続kill疑い、空detailと、ローカルで確認した不要な129 MiBのモデル系メモリ削減を合わせると、OOM仮説は強く支持される。

## before / after 出力突合

`computed_at` / `generated_at` だけを除き、JSONをkey順・compact形式に正規化した。

- before SHA-256: `3591e1a960cb5314edfd9094f2e34fe74d9f2cce193b73944010a3f92daa4e79`
- after SHA-256: `3591e1a960cb5314edfd9094f2e34fe74d9f2cce193b73944010a3f92daa4e79`
- 候補数: before 12 / after 12

少数レース突合:

| race_id | level | title | bet | 結果 |
|---|---|---|---|---|
| `20260818-13-02` | `amagasaki_win3_ace_kimarite_no_rain` | 尼崎 3単 雨除外 401.6% | 単勝 3 | 一致 |
| `20260818-13-09` | `amagasaki_win3_ace_kimarite_late` | 尼崎 3単 後半エース決まり手 422.3% | 単勝 3 | 一致 |
| `20260818-13-12` | `morning_watch_tri143_a12` | 朝監視 尼崎 1-4-3 | 3連単 1-4-3 | 一致 |
| `20260818-14-07` | `naruto_win4_ace_kimarite_all` | 鳴門 4単 エース決まり手 484.9% | 単勝 4 | 一致 |
| `20260818-14-10` | `naruto_win3_ace_kimarite_late_no_rain` | 鳴門 3単 後半雨除外 285.6% | 単勝 3 | 一致 |

## 変更点

1. signal-refresh失敗detail
   - `run_py_detailed()` がexit code、timeout、stderr末尾800文字を保持する。
   - signal prewarm失敗時は `task_runs.detail` に `exit_code=... oom_suspected=... stderr_tail=...` を必ず記録する。stderrが空でも `<empty>` を残す。
   - exit 137 / -9は `oom_suspected=true` と明示する。
2. market-signalsメモリ削減
   - strategy prewarmアプリを常に `_CachedOnlyPredictor` で構築し、`src.web.predictor` とモデルをimport/loadしない。
   - 市場シグナルrouteのDB・判定・L4コードは未変更。
3. TOP degrade経路
   - maintenance snapshotはsignals失敗時もTOP snapshotを生成する。
   - `--signals-degraded` で `degraded=true` / `degraded_reason=signal_refresh_failed` を埋める。
   - 新しいsignal payloadが空なら、同日TOP snapshotの直近signalsを保持し、`degraded_source=same_day_top_last_good` を付ける。既存last-good cacheがある場合は通常のsnapshot builderがそれを優先する。

## テスト・静的確認

- focused: `139 passed`
- 指定全件: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_signal_oom_full`
  - `1097 passed, 1 skipped`（既存pytest cache ACL warning 1件）
- scoped Python compile: pass
- changed smaller files Ruff（既存unusedを除外）: pass
- `git diff --check`: pass
- `src/web/app.py`全体Ruffは既存99 findingsがあり非green。今回差分とは分離し、L4ロジックをlint目的で変更していない。

## 任意調査

- 8/17の結果不完全1レースは `20260817-12-12`（住之江12R）。
- `race_results` は1〜5号艇の5行で、6号艇 4826 井上一輝の行だけ欠落。払戻は3連単 `1-2-3` 等が存在する。
- 本番DBへの補完書込み・外部再取得は実施していない。

## 残課題

- 任意4のmorning guardianへのmarket-signalsバックアップ追加は未実施。今回の必須修理と分離して検討する。
- Render上の実RSSと次回夜間 `render_signal_refresh_*` 成功はデプロイ後に読取り確認が必要。本作業ではpush/deployしていない。
- 住之江12Rの6号艇が公式上の非数値着（転覆・失格等）か、parser欠落かを公式結果から確認し、必要なら別タスクでfail-closedな取込み修正を行う。

## コミット

- 実装コミットID: `PENDING`（ローカルcommit作成後、後続ログ確定commitで追記）
- push / deploy: 実施なし
