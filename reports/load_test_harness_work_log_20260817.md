# 負荷テスト用ハーネス 作業ログ（2026-08-17）

## 目的と安全境界

外部公開前に本番 Web の同時アクセス耐性を段階的に確認するため、`scripts/load_test.py` を構築する。
計測ワークロードは GET のみ。例外は認証開始時の CSRF 取得後に行う `/login` への POST 1回だけで、
ログイン後は同じセッション cookie を全 GET で再利用する。パスワードは
`BOATRACE_MEMBER_PASSWORD` 環境変数以外から取得・保存・表示しない。

## 実装した安全機構

- 段階ランプ、段ごとの短い計測時間、同時数キャップ（既定および絶対上限100）。
- 同時20以上は `--allow-high-concurrency` がなければ実行前に拒否。
- エラー率が10%を超える、または p95 が15秒を超えると、その段の進行中リクエストを停止し、後続段へ進まない。
- 既定の送信間隔は仮想ユーザーごとに1秒。固定された本番 Web の GET だけを使用する。
- auth on でも環境変数が未設定なら auth off にフォールバックし、公開 `/healthz` だけを測定する。
- 認証時の race detail は、当日の `/races` HTML から正規形式の当日 race_id を発見できた場合だけ使用する。
  発見できなければ存在しない ID を生成せず、安全に中止する。
- HTTP ステータスは2xxだけを成功扱いにし、ログイン切れのリダイレクトも失敗として停止判定に含める。

## 使い方

軽い公開スモーク（今回 Codex が実行する唯一の本番負荷確認）:

```powershell
.\.venv\Scripts\python.exe scripts\load_test.py --stages 2,5 --stage-seconds 10 --base-url https://boatrace-web.onrender.com --auth off
```

auth on の手順（PowerShell。値をコード、引数、ログへ書かない）:

```powershell
$env:BOATRACE_MEMBER_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
.\.venv\Scripts\python.exe scripts\load_test.py --stages 2,5,10 --stage-seconds 25 --auth on
Remove-Item Env:BOATRACE_MEMBER_PASSWORD
```

リンによる安全確認後の本格ランプ例。Render の Web/DB メトリクスとログを同時監視し、異常時は
`Ctrl+C` でも即時中断する。Codex はこのコマンドを実行していない。

```powershell
$env:BOATRACE_MEMBER_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
.\.venv\Scripts\python.exe scripts\load_test.py --stages 2,5,10,20,40 --stage-seconds 25 --max-concurrency 40 --auth on --allow-high-concurrency
Remove-Item Env:BOATRACE_MEMBER_PASSWORD
```

停止条件は既定で `error_rate > 0.10` または `p95 > 15秒`。変更する場合は
`--error-rate-stop` と `--p95-stop-sec` を使う。結果は常に
`reports/load_test_result_<UTC timestamp>.json` に保存され、stdout に段別表、安全に捌けた最大同時数、
knee、ボトルネック仮説を表示する。サーキットブレーカー作動時の終了コードは2、準備・認証失敗は1。

## 検証記録

- ネットワーク無し単体テスト: `tests/test_load_test_harness.py` 18件成功。段生成/上限、停止判定、
  p50/p95/p99、実在当日race_id抽出、公開/認証mix、高同時数ガード、1回だけのログインを検証。
- 必須 non-E2E pytest: `1088 passed, 1 skipped`。実行コマンドは
  `.\.venv\Scripts\python.exe -m pytest tests\ -q --ignore=tests/e2e --ignore=tests/round3_e2e`（専用basetempを追加）。
- Python compile、対象ファイルの Ruff: 成功。
- 本番軽量スモーク（同時2,5、各10秒、auth off、`/healthz` のみ）:
  - 同時2: 18 requests、18成功、0失敗、error 0.0%、p50 0.108秒、p95 0.288秒、
    p99 0.341秒、1.80 req/s、status `200: 18`。
  - 同時5: 45 requests、45成功、0失敗、error 0.0%、p50 0.106秒、p95 0.247秒、
    p99 0.401秒、4.50 req/s、status `200: 45`。
  - 安全に捌けた最大同時数: 5。knee: この軽量範囲では未観測。停止条件作動なし。
  - 結果: `reports/load_test_result_20260817T074047Z.json`。
- 初回のsandbox内試行は外部接続が許可されず、同時2の最初の2件が `ConnectError` となった時点で
  サーキットブレーカーが即停止した。本番接続前の環境要因であり、失敗artifactは削除対象として
  handoffに記録。許可後の上記スモーク以外に本番負荷はかけていない。
- 同時20以上のランプ: **未実施（リンの安全確認・監視付き実行待ち）**。
- push / deploy / scheduler / Supabase writer: **未実施**。
