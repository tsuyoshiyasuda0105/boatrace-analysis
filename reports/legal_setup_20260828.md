# 法定ページの反映と、事業者確定情報の埋め方 (2026-08-28)

## 提供されている草案 (src/web/legal_drafts/)

| ファイル | ページ | 条数 |
|---|---|---|
| APP_TERMS_DRAFT.md | /legal/terms (利用規約) | 39 |
| PRIVACY_POLICY_DRAFT.md | /legal/privacy (プライバシーポリシー) | 23 |
| DISCORD_COMMUNITY_RULES_DRAFT.md | /legal/discord (Discord参加・投稿規約) | 22 |
| TOKUSHOHO_AND_CHECKOUT_DRAFT.md | /legal/tokushoho (特定商取引法に基づく表記) | (表形式) |

各ページの黄色いハイライトが未確定項目です。事業者本人と専門家の確認後、
下記の環境変数を Render の boatrace-web に設定すると自動で埋まります。

## Render で設定する環境変数

Render → boatrace-web → Environment に順次追加してください。
値の間違いが心配な場合は、まず未設定のままで公開し、順次埋めるのでも構いません
(未確定の項目は黄色く目立って表示されるので、公開後に気付けます)。

### 事業者情報 (必須)
- `LEGAL_OPERATOR_NAME`     … 事業者名 (法人名 又は 個人事業者名)
- `LEGAL_RESPONSIBLE_PERSON` … 代表者名 又は 運営統括責任者名
- `LEGAL_ADDRESS`           … 所在地 (郵便番号を含む完全な住所)
- `LEGAL_PHONE`             … 電話番号 (「請求に応じて開示」方式の場合は専門家確認要)
- `LEGAL_EMAIL`             … 問い合わせ用メールアドレス

### サービス情報
- `LEGAL_SERVICE_NAME`      … サービス正式名称
- `LEGAL_PLAN_NAME`         … 有料プラン名
- `LEGAL_PRICE`             … 販売価格 (例: 月額1,380円（税込）)
- `LEGAL_PLAN_FEATURES`     … 提供内容 (例: バックテスト100回/日、Discord参加権限)
- `LEGAL_SERVICE_PERIOD`    … 利用可能期間
- `LEGAL_FREE_TRIAL`        … 無料期間 (実施しないなら「実施しません」)

### 契約・決済
- `LEGAL_BILLING_ANCHOR`    … 自動決済日 (例: 毎月の契約応当日)
- `LEGAL_CANCEL_DEADLINE`   … 解約期限 (例: 更新日の24時間前)
- `LEGAL_REFUND_POLICY`     … 返金方針
- `LEGAL_RETENTION_PERIOD`  … データ保存期間

### その他
- `LEGAL_MAINTENANCE_WINDOW` … 定期メンテナンス時間 (現行案: 毎日4:00から7:00まで)
- `LEGAL_JURISDICTION`      … 管轄裁判所
- `LEGAL_ANALYTICS_VENDORS` … 利用する解析サービス
- `LEGAL_EXTERNAL_VENDORS`  … 委託する外部事業者と保存国
- `LEGAL_EFFECTIVE_DATE`    … 制定日・施行日・最終改定日

## 環境変数で埋まらない場合

上記のマッピングにない プレースホルダー (例: 個別の実装細目) は、
草案 markdown を直接編集してください。Render は変更を自動で拾います。

## 公開前チェック

1. `/legal/terms` `/legal/privacy` `/legal/tokushoho` `/legal/discord` を開き、
   黄色いハイライトが 0 件になっていることを確認する
2. 事業者本人と、日本法に詳しい専門家の最終確認を受ける
3. データ提供元 (BOAT RACE 振興会) への商用利用の許諾を確認する
