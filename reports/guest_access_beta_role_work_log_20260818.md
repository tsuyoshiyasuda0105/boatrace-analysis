# ゲスト開放 + ベータ会員ロール 作業ログ（2026-08-18）

## 変更点

- `ROLE_RANK` に `beta_member: 15` を追加し、`normalize_role()` が保持することを確認した。
- Supabase会員ロール集合と2つのセッション設定経路へ `beta_member` を追加した。ログイン直後・テストロール設定・ロール再取得後のいずれも `is_member=True` になる。
- `can_use_backtest()` を新設し、バックテスト画面と全APIの共通門番 `_paid_member_api_forbidden()` を同じ機能許可へ変更した。`beta_member` / `paid_member` / `admin` は許可、`free_member` は拒否される。既存の読み取り専用 `test_viewer` も維持した。
- `/`、`/races`、`/race/<race_id>` を `BOATRACE_GUEST_ACCESS`（既定 `1`）経由で公開した。値はリクエストごとに読み、`0` へ変えるとプロセス再起動なしでゲストをログインへ転送する。
- `/` はリダイレクトではなく当日の一覧を直接返し、未ログインでもHTTP 200にした。
- ゲストの一覧は保存TOPスナップショットだけを返し、ミス時はHTTP 200の一時表示へ即時フォールバックする。ゲストからレース全走査、自己修復収集、TOPスナップショット書込みへは進まない。
- ゲストの個別詳細は保存HTMLキャッシュだけを読み、`recompute=1` とプロセス側の再計算許可が同時に存在してもライブ構築へ進まない。
- 既存の会員向けHTMLをゲストへ流用しないため、詳細HTMLキャッシュを `v17` へ更新した。共有詳細HTMLのヘッダーを権限中立化し、ROI会場警告・SWEET SPOT・三連単ROIラベルをゲスト共有HTMLから除外した。
- ゲストTOPでは市場シグナル・ROI候補バッジ・EV+・採用戦略を空にし、会員向けL4/ROI凡例をレンダリングしない。ヘッダーには会員メニューと `/member/today-races` 導線を出さない。
- `/member/today-races` は会員限定、ROI・月別推移・健全度・事故率・展示精度・管理・`/public/roi` はadmin限定の既存デコレータを変更していない。cron構成も変更していない。

## ゲスト経路の実測

条件: ローカル `data/boatrace.db` を SQLite URI `mode=ro` で接続、Webは `cached_predictions_only=True`、DB初期化無効、HTTPプロファイル有効。本番Supabaseへの接続・書込みは行っていない。

| 経路 | 状態 | HTTP | wall | Flask計測 | SQL | SQL時間 | 応答 |
|---|---|---:|---:|---:|---:|---:|---:|
| `/` | 保存TOPスナップショット初回読込 | 200 | 28.714 ms | 27.0 ms | 1 | 2.2 ms | 114,822 bytes |
| `/races?date=2026-08-18` | 同一プロセスの保存TOPスナップショット | 200 | 3.010 ms | 2.6 ms | 0 | 0.0 ms | 114,822 bytes |
| `/race/20260818-01-01?recompute=1` | v17保存HTMLキャッシュ強制ミス | 200 | 3.420 ms | 3.1 ms | 2 | 1.5 ms | 4,737 bytes |

個別詳細キャッシュミスは `Retry-After: 30` 付きの準備中ページへ即時フォールバックした。レース基本情報のライブ構築、保存予測の展開、モデル推論、全レース走査は実行されていない。3.420 msで重くないため、追加の保存予測簡易ページ構築は不要と判断した。

## テスト結果

- 焦点テスト（権限、認証、バックテスト、詳細キャッシュ、TOP、DB劣化）: **117 passed**。
- 指定の全非E2Eテスト: **1105 passed, 1 skipped**（既存1098件 + 新規7件）。
  - 実行: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_guest_beta_full`
- Pythonコンパイル: pass。
- Ruff（変更した小規模Pythonファイルと新規テスト）: pass。
- `git diff --check`: pass（既存CRLF警告のみ）。
- 新規テストで、ゲスト3画面の200、会員/admin限定経路の302/403、betaの画面/API許可、freeの画面/API拒否、リクエスト時kill switch、ゲストHTMLの会員情報非表示、ゲストキャッシュミス時の重処理不実行を検証した。

## 残課題

- `beta_member` のユーザーへの付与運用・本番データ変更は本作業の対象外。コードはロール値を受理できる状態にした。
- デプロイ後は背景プリウォームが新しい詳細HTMLキャッシュ `v17` を順次生成する。それまでは未生成レースが安全な準備中表示になる。
- 本番負荷試験、push、デプロイは指示どおり未実施。

## コミットID

- 実装コミット: `PENDING`
