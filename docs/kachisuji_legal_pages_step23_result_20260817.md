# 勝ち筋サーチ Step 23 実装結果

作成日: 2026-08-17

## 結果

有料サービス向けの公開ページとして、利用規約、特定商取引法に基づく表記、プライバシーポリシーを追加した。全ページは認証なしで表示できる。事業者情報および価格等は環境変数から取得し、未設定値は明示的な入力用プレースホルダに置き換え、ページ上部に未完成警告と不足している環境変数名を表示する。

事業者の氏名、住所、電話番号、メールアドレス、価格その他の個別情報は入力していない。各ページ内のHTMLコメントにも、リーガルチェック前の雛形であることを記載した。

## 追加ファイルと行数

- `src/web/legal_bp.py`: 91行
- `src/web/templates/legal_terms.html`: 78行
- `src/web/templates/legal_tokushoho.html`: 47行
- `src/web/templates/legal_privacy.html`: 69行
- `tests/test_kachisuji_legal_pages.py`: 130行
- `docs/kachisuji_legal_pages_step23_result_20260817.md`: 78行

## 変更した既存ファイルと行数

- `src/web/app.py`: 1行追加（Blueprint登録のみ。現在21,911行）
- `src/web/templates/base.html`: 5行追加（フッターの3リンクのみ。現在171行）
- `src/web/billing.py`: 変更なし
- 既存ルート: 変更なし

## リッキーさんが入力する情報

本番公開前に、次の値を環境変数へ設定し、ページ表示と契約条件の整合を確認する。

- [ ] `LEGAL_OPERATOR_NAME`: 販売事業者名
- [ ] `LEGAL_RESPONSIBLE_PERSON`: 運営統括責任者名
- [ ] `LEGAL_ADDRESS`: 所在地
- [ ] `LEGAL_PHONE`: 電話番号
- [ ] `LEGAL_EMAIL`: 問い合わせ用メールアドレス
- [ ] `LEGAL_PRICE`: 販売価格（税込）。Stripe Priceの表示金額・課金周期と一致させる
- [ ] `LEGAL_ADDITIONAL_FEES`: 商品代金以外に利用者が負担する料金
- [ ] `LEGAL_PAYMENT_METHOD`: 支払方法
- [ ] `LEGAL_PAYMENT_TIMING`: 支払時期
- [ ] `LEGAL_SERVICE_START`: サービス提供時期
- [ ] `LEGAL_REFUND_POLICY`: 返品・キャンセル・返金条件。Stripeの設定と一致させる
- [ ] `LEGAL_SYSTEM_REQUIREMENTS`: 対応ブラウザ、端末等の動作環境
- [ ] `LEGAL_JURISDICTION`: 第一審の専属的合意管轄裁判所
- [ ] `LEGAL_EFFECTIVE_DATE`: 制定日・最終改定日

## Stripe導線で必要な後続変更

現状のアプリ内には `/billing/checkout` へ送信する申込画面またはフォームが見当たらず、`billing.py` はPOSTルートから直接Stripe Checkout Sessionを作成している。今回の変更範囲では同ファイルを変更していない。

有料受付を開始する前に、次の対応が必要となる。

1. 料金、更新周期、解約時期、返金条件と3つの法定表示ページへのリンクを示す申込画面を用意する。
2. 利用規約とプライバシーポリシーを読んで同意する操作を、申込前またはStripe Checkout上で要求する。画面側だけでなく、未同意の直接POSTを受理しない仕組みが必要となる。
3. Stripe Checkoutの利用規約同意収集機能を使う場合は、Stripe側の公開ビジネス情報と規約URLを設定し、Checkout Session作成時の同意収集設定を追加する。
4. 同意日時、同意した規約版、利用者識別子を証跡として保持する要件を決める。保存を行う場合は、別作業としてデータ設計、`billing.py`またはWebhook処理、保持期間をレビューする。
5. `LEGAL_PRICE`の表示と`STRIPE_PRICE_ID`が参照する金額・通貨・課金周期が一致することを、本番設定で照合する。

## 既知の制限と確認事項

- 文面はリーガルチェック未実施の雛形であり、公開前に事業者本人と専門家による確認が必要である。
- 既存アプリの本番メンテナンスゲートは04:00〜07:00である一方、今回の指定に従った利用規約は毎日24:00〜翌6:00の停止を記載している。運用時間と文面を公開前に一致させる必要がある。
- 既存の本番メンテナンスゲートでは法定表示ページも04:00〜07:00に503応答となる。常時閲覧を要件とする場合は、別作業でメンテナンス除外パスを検討する必要がある。
- 環境変数の未設定はページ上で検出できるが、アプリ起動またはデプロイを停止する仕組みは今回の範囲に含めていない。
- 外部サービス名および個人情報の実際の取扱いが文面と一致するか、公開前に運用確認が必要である。

## テスト結果

- 焦点テスト: 11 passed
- 全非E2E回帰: 1,059 passed / 1 skipped
- メインE2E: 77 passed
- Round 3 E2E: 3 passed
- Pythonコンパイル: 成功
- Ruff: 成功
- `git diff --check`: 成功
- 法定表示3テンプレートの禁止対象3表現の文字列検査: 0件
- `src/web/billing.py`差分: なし

DB書込み、Supabase接続、ネットワークアクセス、デプロイ、push、ローカルスケジューラー起動は行っていない。
