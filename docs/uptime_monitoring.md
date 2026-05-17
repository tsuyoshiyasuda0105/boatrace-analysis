# 外部死活監視 (UptimeRobot) セットアップ

Render の自動デプロイ + ローカル PC バッチの両方を **外部から監視** して、
障害が起きたら即通知を受け取る仕組みです (backlog item 2)。

## 監視ポイント

| 対象 | URL | 期待コード | 異常時の意味 |
|---|---|---|---|
| Render Web | `https://your-site.onrender.com/healthz` | **200** | サイトダウン or DB 障害 or データ品質 error |
| Render Web (top) | `https://your-site.onrender.com/login` | **200** | サイト自体のダウン |

`/healthz` は内部で以下をチェックして JSON で返します:
- DB 接続 (Supabase)
- 今日の `system_status` テーブルに `error` レコードがあるか
- モデルロード状況

**判定**:
- 全 OK → HTTP 200
- DB 接続失敗 or data_quality_errors >= 1 → HTTP 503

## UptimeRobot 設定手順 (無料、5 分)

### 1. アカウント作成
1. https://uptimerobot.com/signUp にアクセス
2. メールアドレス + パスワードで登録 (無料プランで OK)
3. メール認証

### 2. モニター登録

無料プランで最大 **50 個まで監視可**。本案件には 2 個で十分。

#### Monitor #1: Render Web (詳細チェック)
```
Monitor Type   : HTTP(s)
Friendly Name  : Boatrace Web Health Check
URL            : https://<あなたのサイト>.onrender.com/healthz
Monitoring Interval: 5 minutes (無料プラン最短)
Monitor Timeout: 30 seconds
HTTP Method   : GET
```

- 「Advanced Settings」で **HTTP Status Code: 200** のみ OK と設定
- 503 が返ると即 DOWN 判定

#### Monitor #2: Render Web (簡易チェック、複数監視で冗長性)
```
Monitor Type   : HTTP(s)
Friendly Name  : Boatrace Web Login
URL            : https://<あなたのサイト>.onrender.com/login
Monitoring Interval: 5 minutes
```

### 3. 通知設定

「My Settings → Alert Contacts」で通知先を追加:

#### おすすめ: Gmail に通知
- Type: E-mail
- 既存の Gmail を登録

#### 即時性重視: LINE Notify
- Type: Webhook (LINE Notify トークン経由)
- Webhook URL: `https://notify-api.line.me/api/notify`
- Custom HTTP Headers: `Authorization: Bearer <あなたのトークン>`
- POST Value: `message=Boatrace Web ダウン: *monitorURL*`

#### 即時性重視: Slack
- Type: Webhook (Incoming Webhook URL)

#### 即時性重視: Discord
- Type: Webhook (Discord チャネルの Webhook URL)
- `?wait=true` を付ければステータス確認可

### 4. アラート閾値設定

各モニターの「Alert Contacts」で:
- **Send a notification when monitor is DOWN** (異常検知時) → ON
- **Send a notification when monitor is UP** (復旧時) → ON
- **Send only after** (誤検知防止) → 2 minutes 等

## ローカル PC の死活監視について

PC が止まると `/healthz` 経由では検知できません (PC が止まっても Render は動き続けるため)。
PC 監視するには以下のいずれか:

### 方法 A: バッチが Render に「生きてます」と通知 (heartbeat)
バッチ実行ごとに Render の `/api/heartbeat` 等にリクエストを送り、
Render 側で最後の heartbeat 時刻を記録。古ければ `system_status` に warning。

### 方法 B: UptimeRobot の Cron Monitor (有料)
`heartbeat.uptimerobot.com/<id>` に定期的に ping を送る方式。
UptimeRobot 側で期待間隔より遅れたら通知。**有料プラン** 必要。

### 方法 C: PC を 24h 起動するなら、Windows Event Log 監視
PowerShell スクリプトで定期的に最終ファイル更新時刻チェック、
古ければメール送信。要 PC 設定。

→ **当面は方法 A を推奨**。実装はまだ未着手なので、必要なら別途依頼してください。

## 動作確認

UptimeRobot 登録後、すぐに最初のチェックが走ります。
ダッシュボードで「Up」になっていれば OK。

故意に DOWN にするテスト:
1. Render dashboard で Web Service を一時停止
2. 数分で UptimeRobot から「DOWN」通知が来る
3. 復旧して「UP」通知が来る

## トラブルシューティング

- **常に DOWN になる**: `/healthz` が 503 返してる可能性
  - ブラウザで `/healthz` 直接アクセスして JSON 中身確認
  - `data_quality_errors >= 1` だと 503
  - 今朝の null racer_number 等のデータ品質 error が原因の場合あり

- **DB エラーで 503**: Supabase connection 切れてる
  - Render dashboard で再起動
  - `DATABASE_URL` 環境変数確認

## 関連

- `/admin/cache-clear` : Render 内メモリキャッシュ手動クリア (POST + CSRF)
- `scripts/check_data_quality.py` : データ品質チェック (`system_status` 更新)
- `scripts/db_size_check.py` : DB 容量監視 (`system_status` 更新)
