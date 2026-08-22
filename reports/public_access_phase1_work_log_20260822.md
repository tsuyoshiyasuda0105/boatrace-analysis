# 公開前アクセス制限強化 第1段階 作業ログ

作業日: 2026-08-22

対象指示書: `reports/public_access_phase1_spec_20260822.md`

## 結果

公開範囲や認証要件を一切変更せず、ゲスト向けレート制限、429応答、検索エンジン制御だけを強化した。`@login_required` / `@admin_required` の付け外し、採用ROI戦略、展示データ反映、`render.yaml` は変更していない。push、デプロイ、本番Supabase書込みも行っていない。

## 変更点

- 通常ページの `BOATRACE_GUEST_RATE_LIMIT` 既定値を120回/60秒から40回/60秒へ変更した。既存どおり環境変数で上書きでき、0以下で無効化できる。
- `/api/` 用に独立したIP別バケットと `BOATRACE_GUEST_API_RATE_LIMIT` を追加し、既定値を15回/60秒とした。通常ページのアクセスでAPI枠を消費せず、APIアクセスでも通常ページ枠を消費しない。
- 非API経路で `Accept` がHTMLを明示的に優先する場合、429で `rate_limited.html` を返すようにした。画面には「アクセスが集中しています。しばらくお待ちください。」を表示し、`Retry-After` に合わせて再読込する。API経路は `Accept` にかかわらず従来どおりJSONを返す。
- 429応答はHTML・JSONとも `Retry-After` と `Cache-Control: no-store` を維持する。
- `/robots.txt` は既定で `User-agent: *` / `Disallow: /` を返す。`BOATRACE_ALLOW_INDEXING=1` の場合だけ `Allow: /` に切り替える。
- `/robots.txt` をゲストレート制限から除外した。既存の `/healthz` と `/static/` の除外も維持した。

## 数値の根拠

- 通常ページ40回/60秒: 1.5秒に1回の連続操作に相当し、通常の一覧・詳細閲覧や再読込には余裕を残す一方、既定120回/60秒（0.5秒に1回）より機械的な巡回を早く抑止できる。指示書で指定された公開前の防御水準を採用した。
- API 15回/60秒: 4秒に1回の継続呼び出しに相当する。HTMLページより自動取得・連打されやすく、1リクエストあたりのデータ取得密度も高いため通常ページの37.5%に抑えた。指示書の提案値を採用し、用途別バケットに分離して通常閲覧への巻き込みを防いだ。
- 制限窓60秒: 既存設計を維持した。環境変数名と運用方法の互換性を保ち、第1段階の変更範囲を必要最小限にするためである。

## テスト結果

- 必須機能集中テスト: 16 passed
  - 通常40回 / API15回の既定値
  - 通常/APIの独立バケット
  - 会員の除外
  - HTML 429 / API JSON 429
  - robots.txtの既定Disallowと環境変数によるAllow
  - `/healthz`、`/static/`、`/robots.txt` の除外
- 指定の全回帰テスト:
  - コマンド: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - 結果: **1200 passed, 1 skipped**（基準1198 passedを維持し、新規回帰2件を追加）
  - 既存の `.pytest_cache` ACL警告1件のみ。テスト失敗なし。
- 静的確認:
  - `src/web/app.py` と変更テストのPythonコンパイル成功
  - 変更テストのRuff未定義名チェック成功
  - `git diff --check` 成功
  - `render.yaml` 差分なし
  - `@login_required` / `@admin_required` の追加・削除差分なし

## コミット

- 実装コミット: `4e8db6b` (`Harden guest access before public launch`)
- 実装コミット対象: `src/web/app.py`、`src/web/templates/rate_limited.html`、`tests/test_security_hardening.py`
- push・デプロイは未実施。

## 補足

最初の集中テストではテスト用lambdaルートのendpoint名衝突が3件発生したため、固有endpoint名を付けて再実行し16件すべて通過した。別の83件束ね実行はDBプール系の待機中に180秒上限へ達したが、アサーション失敗はなく、最終的に指定の全1200件を36.13秒で完走して確認した。
