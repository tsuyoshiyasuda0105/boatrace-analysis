# 作業指示書: サイト公開前セキュリティ強化 (Codex CLI 用)

作成: 2026-08-19 / 発注: リッキー / 監査・検品・管理: リン (Claude)
リポジトリ: C:\boat_project\boatrace-analysis (本番 Render + Supabase Postgres)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1118 passed, 1 skipped。割らないこと)

## 背景

ゲスト開放 (56439e7) によりサイトが一般公開になるため、リンが事前監査を実施した。
**健全な点 (変更不要)**: SQL はプレースホルダ使用 (サンプル確認)・ログインロックアウト
(15分10回/30分ロック)・hmac.compare_digest による定数時間比較・セッション再生成
(session.clear)・Cookie フラグ (Secure/HttpOnly/SameSite=Lax)・主要セキュリティヘッダー
(CSP/X-Frame-Options DENY/HSTS/nosniff/Referrer-Policy)・Stripe webhook 署名検証・
admin ページの @admin_required・cache-clear の CSRF トークン・テンプレートに |safe なし・
MAX_CONTENT_LENGTH 1MB。

以下は監査で見つかった**要修正/要検証**項目。優先度順に対応すること。

## HIGH (公開前に必須)

### H1: 本番で dev 用シークレットのまま起動できてしまう
src/web/app.py 6564- 付近。`WEB_SESSION_SECRET == "dev-only-do-not-use-in-prod"` が
本番 (is_production) でも **logger.critical を出すだけで起動継続**する。既知のキーで
セッション署名が偽造可能 = 会員/管理者へのなりすましに直結する。
- 修理: 本番では **起動を拒否** (RuntimeError) する。`WEB_MEMBER_PASSWORD` の
  dev デフォルト ("dev-member") も同様に本番起動拒否。
- healthz だけは受けたい等の事情があれば、起動拒否の代わりに全リクエスト 503 でも可。
- テスト: 本番フラグ + デフォルト秘密で create_app が失敗することをモックで保証。

### H2: /admin/cache-clear が @login_required (全会員が叩ける)
src/web/app.py 22139。docstring は「会員限定」だが、ゲスト開放後は free/beta 会員も
含まれる。全キャッシュクリアは**キャッシュストンピング → 昨日の 230秒障害の再現装置**
になる (自己 DoS)。
- 修理: @admin_required に変更。テストで beta/paid が 403 になることを保証。

### H3: プロキシ配下の client IP 取り扱いの検証
ログインロックアウトは IP キー。Render はリバースプロキシ配下のため、
`request.remote_addr` が実クライアント IP になっているか (ProxyFix / X-Forwarded-For の
信頼設定) を**確認**すること。
- 未設定なら: 全員が同一 IP に見え、攻撃者1人のロックアウトが**全ユーザーの
  ログインを 30 分止める** (DoS)。werkzeug ProxyFix (x_for=1) を導入。
- 設定済みなら: 作業ログに確認結果を記録するだけでよい。

## MEDIUM (公開初週内)

### M1: f-string SQL 65 箇所の全数監査
`execute(f"` が src/ と scripts/ に 65 箇所。サンプル確認ではプレースホルダ生成のみの
安全なパターンだったが、**全数を機械的に確認**し、ユーザー入力が文字列補間される箇所が
1 つも無いことを保証する。
- 修理: 全数を確認して作業ログに一覧 (ファイル:行 → 判定) を記録。
  加えて、tests/test_source_regression.py に「f-string SQL にユーザー入力変数を
  補間しない」静的チェック (危険パターンの grep ベースで可) を追加し、将来の混入を防ぐ。

### M2: ゲスト経路の簡易レート制限
ゲスト開放で /races, /race/<id>, 公開 API が無制限アクセス可能になった。応答は
キャッシュ済みで軽いが、悪意あるクローラ対策として**per-IP の簡易スロットル**
(例: 60秒に120リクエスト超で 429、インメモリで可、healthz/static 除外) を追加。
- 制約: 正規ユーザーに誤爆しない緩い閾値にする。環境変数で無効化可能に
  (BOATRACE_GUEST_RATE_LIMIT=0)。

### M3: 500 応答の内部情報漏えい確認
今朝の incident に「the pool 'pool-1' has already 12 requests waiting」等の内部詳細が
ある。handle_500 が**ユーザー向けレスポンスに内部メッセージを含めていないか**確認し、
含めていれば汎用文言に修正 (ログには従来どおり詳細を残す)。

## LOW (記録のみ・今回は着手しない)

- CSP の script-src 'unsafe-inline' の排除 (nonce 化) — 大規模改修のため backlog
- ログインロックアウトがプロセス内メモリ (再起動でリセット・2インスタンス非共有) —
  現規模では許容。将来 DB バックドに
- /healthz の revision 表示 — 許容

## 絶対ルール
- origin/main へ push 禁止・デプロイ禁止 (リンが実施)
- 本番 Supabase への書込み・スキーマ変更禁止 (調査は読取りのみ)
- 採用ROI戦略の判定結果を変えない / cron 構成を増減しない
- **挙動を変えるのはセキュリティ上必要な箇所だけ**。リファクタ禁止
- 作業ログ: reports/security_hardening_work_log_20260819.md
  (各項目の判定・変更点・テスト結果・残課題・コミットID)

## 受け入れ条件
- [ ] H1: 本番 + デフォルト秘密で起動拒否 (テストあり)
- [ ] H2: cache-clear が admin 限定 (テストあり)
- [ ] H3: ProxyFix の要否判定と対応 (作業ログに根拠)
- [ ] M1: 65 箇所の判定一覧 + 回帰ガード追加
- [ ] M2: ゲストレート制限 (閾値・無効化スイッチ・テスト)
- [ ] M3: 500 応答の確認結果と必要なら修正
- [ ] pytest 1118+ passed / push なし / デプロイなし
