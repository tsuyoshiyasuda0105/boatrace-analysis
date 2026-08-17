# 勝ち筋サーチ Step 24 実装結果

作成日: 2026-08-17

## 結果

ログイン済み会員向けに `GET /signup/plan` を追加し、有料プランの表示条件、法定3ページ、同意チェック、Stripe Checkoutへの導線を1画面にまとめた。価格および契約条件は環境変数から取得し、未設定値はプレースホルダと警告で明示する。`LEGAL_PRICE` または `STRIPE_PRICE_ID` が未設定の場合は申込操作を無効化する。

`POST /billing/checkout` には、現在の規約版への明示的な同意を要求する検証だけを追加した。未同意、同意値の欠落、規約版の欠落・不一致はStripeクライアントを取得する前にHTTP 400で拒否する。同意済みの場合は既存のStripe Checkout Session生成、303リダイレクト、Customer Portal、Webhook処理を変更せずそのまま使う。

## 追加ファイルと行数

- `src/web/signup_bp.py`: 70行
- `src/web/templates/signup_plan.html`: 143行
- `tests/test_kachisuji_signup_consent.py`: 197行
- `docs/kachisuji_signup_consent_step24_result_20260817.md`: 110行

## 変更した既存ファイルと行数

- `src/web/billing.py`: 3行追加（ヘルパimport 1行、`billing_checkout` 冒頭の検証2行。Stripe呼出し本体・portal・webhookは変更なし）
- `src/web/legal_bp.py`: 2行追加（`TERMS_VERSION = "2026-08-17"`）
- `src/web/app.py`: 1行追加（Signup Blueprint登録のみ）
- `src/web/templates/base.html`: 3行追加（会員ナビの申込リンクのみ）

## 同意検証の実装箇所

- `src/web/signup_bp.py::checkout_consent_is_valid`: formまたはJSONから `agree_terms` と `terms_version` を取得し、明示的な真値かつ `TERMS_VERSION` と一致する場合だけ通過させる。
- `src/web/billing.py::billing_checkout`: 既存の `stripe_configured()` 判定および `_stripe()` 呼出しより前に上記ヘルパを1回呼び、失敗時は `consent_required` と日本語メッセージを含む400を返す。
- クライアント側はチェックが入るまでボタンを無効化するが、直接POSTへの防御はサーバー側検証が担う。

## 同意記録の可否とスキーマ変更案

既存の `profiles` は利用者識別・メール・Stripe Customer ID、`user_roles` は権限、`subscriptions` はStripe購読状態を保持する設計であり、同意日時と規約版を保存できる意味的に適切な列はない。このため、本ステップでは同意記録のDB保存を実装していない。DB/Supabaseへの接続、DDL、データ更新も行っていない。

別途承認を受けて実装する場合は、既存テーブルへの流用ではなく、監査証跡を追記保持できる専用テーブルを提案する。

- テーブル例: `paid_plan_consents`
- 必須列: `id`、`user_id`（`auth.users.id`への外部キー）、`terms_version`、`agreed_at`（UTCのタイムゾーン付き日時）
- 任意列: `privacy_version`、`checkout_session_id`、`source`
- インデックス: `(user_id, agreed_at DESC)` および必要に応じて `checkout_session_id`
- 運用: 原則append-only、RLSを有効化し、利用者本人の参照とサーバー側の追加だけを許可する。IPアドレスやUser-Agentは必要性・保持期間・プライバシー方針を決めるまで保存しない。

## 規約改定時の運用

1. `src/web/legal_bp.py` の `TERMS_VERSION` を新しい一意の版へ更新する。
2. `LEGAL_EFFECTIVE_DATE` と法定ページ本文を同じ公開単位で更新し、リーガルレビューを完了する。
3. 旧版の本文、版番号、公開期間を改変せず保管し、どの利用者がどの版へ同意したか追跡可能にする。
4. 申込画面のhidden値とサーバー検証は同じ定数を共有するため、旧画面・旧版のPOSTは400になることをテストする。
5. 既存有料会員へ再同意を求める条件と期限は、規約変更内容および法務判断に基づき別途決定する。

## リッキーさんが設定すべき環境変数（Step 23 + Step 24 完全版）

実値はこのリポジトリやレポートへ記録せず、公開前に表示内容とStripe設定が一致することを確認する。

### Step 23 法定表示

- [ ] `LEGAL_OPERATOR_NAME`: 販売事業者名
- [ ] `LEGAL_RESPONSIBLE_PERSON`: 運営統括責任者名
- [ ] `LEGAL_ADDRESS`: 所在地
- [ ] `LEGAL_PHONE`: 電話番号
- [ ] `LEGAL_EMAIL`: 問い合わせ用メールアドレス
- [ ] `LEGAL_PRICE`: 販売価格（税込）。Stripe Priceの金額・通貨・課金周期と一致させる
- [ ] `LEGAL_ADDITIONAL_FEES`: 商品代金以外に利用者が負担する料金
- [ ] `LEGAL_PAYMENT_METHOD`: 支払方法
- [ ] `LEGAL_PAYMENT_TIMING`: 支払時期
- [ ] `LEGAL_SERVICE_START`: サービス提供時期
- [ ] `LEGAL_REFUND_POLICY`: 解約後の利用期限、日割り返金の有無を含む返品・キャンセル・返金条件
- [ ] `LEGAL_SYSTEM_REQUIREMENTS`: 対応ブラウザ、端末等の動作環境
- [ ] `LEGAL_JURISDICTION`: 第一審の専属的合意管轄裁判所
- [ ] `LEGAL_EFFECTIVE_DATE`: 制定日・最終改定日

### Step 24 申込画面

- [ ] `SIGNUP_PLAN_NAME`: 申込画面に表示するプラン名
- [ ] `SIGNUP_BILLING_CYCLE`: 課金周期（月額等。Stripe Priceと一致させる）
- [ ] `SIGNUP_RENEWAL_POLICY`: 次回請求日・自動更新の考え方
- [ ] `SIGNUP_SERVICE_CONTENT`: 提供内容の要約
- [ ] `SIGNUP_CANCELLATION_METHOD`: カスタマーポータル等の解約方法

`LEGAL_PRICE`、`LEGAL_SERVICE_START`、`LEGAL_REFUND_POLICY` はStep 23とStep 24で同じ値を共有する。

### 既存Stripe決済設定（申込導線の稼働に必要）

- [ ] `STRIPE_SECRET_KEY`: Stripe API秘密鍵
- [ ] `STRIPE_WEBHOOK_SECRET`: Webhook署名シークレット
- [ ] `STRIPE_PRICE_ID`: 申込対象のStripe Price ID
- [ ] `STRIPE_SUCCESS_URL`: Checkout成功後の戻り先（未設定時は既存デフォルトを使用）
- [ ] `STRIPE_CANCEL_URL`: Checkoutキャンセル後の戻り先（未設定時は既存デフォルトを使用）
- [ ] `STRIPE_PORTAL_RETURN_URL`: Customer Portalからの戻り先（未設定時は既存デフォルトを使用）

## 既知の制限

- 同意のサーバー側ゲートは実装済みだが、証跡の永続保存は未実装である。上記スキーマ変更を別途レビュー・承認する必要がある。
- 法定ページ本文および申込条件はリーガルチェック未完了であり、販売開始前に事業者本人と専門家による確認が必要である。
- `LEGAL_PRICE` の表示値と `STRIPE_PRICE_ID` の実際の金額・通貨・課金周期はアプリから自動照合していない。本番設定時に手動照合が必要である。
- 既存のCustomer Portal POSTおよびWebhookの仕様には手を加えていない。

## テスト結果

- 焦点テスト（申込・法定表示・billing登録）: 31 passed
- 全非E2E回帰: 1,070 passed / 1 skipped
- メインE2E: 77 passed
- Round 3 E2E: 3 passed
- Pythonコンパイル: 成功
- Ruff: 成功
- `git diff --check`: 成功
- 未同意POST: HTTP 400、Stripeファクトリ0回、`stripe.checkout.Session.create` 0回
- 同意済みPOST: 既存のline item・user metadataでCheckout Sessionを1回生成しHTTP 303

ネットワーク、Stripe実通信、DB/Supabase変更、デプロイ、push、スケジューラー起動は行っていない。テスト用のローカルfixtureは終了し、確認対象ポートにリスナーは残っていない。
