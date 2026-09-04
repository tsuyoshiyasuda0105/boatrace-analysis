# 自動トリアージ Runbook (スケジュール実行エージェント用)

作成: 2026-08-17 / 発注者: リッキー / 運用: リン (Claude)
目的: 毎朝7:30 + 日中数時間おきに起動し、本番の健全性を点検し、**安全な既知の修復
(prewarm / 再取得) は自動実行**、それ以外は**メール報告のみ**する。

## 絶対ルール (このエージェントの安全則)

1. **自動でやってよいのは「安全・冪等・既知」の修復だけ**:
   - レース詳細ページの prewarm (`scripts/prewarm_race_detail_pages.py`)
   - タグの prewarm (`scripts/prewarm_race_detail_tags.py`)
   - トップスナップショット再生成 (`scripts/build_top_page_snapshot.py`)
   - 市場シグナル prewarm (`scripts/prewarm_strategy_pages.py --mode signals`)
   - 結果再取得 (`scripts/poll_results.py --date <d> --no-jitter`)
   - **いずれも `BOATRACE_TASK_TRIGGER=render-prewarm` を明示**して実行。
2. **やってはいけない (メール報告のみ)**:
   - コード修正・git push・Render デプロイ
   - DB スキーマ変更・データ削除・上書き
   - 環境変数変更・cron 設定変更
   - 上記以外の未知の障害への手当て
   これらは**発注者(リッキー)へメール報告し、承認を待つ**。勝手に実行しない。
3. **スクレイピングを伴う再取得は間隔厳守** (config.REQUEST_INTERVAL_SECONDS=2.0)。
   poll_results / signals prewarm は既存経路を使い、並列しない。
4. **対応した内容は必ず `incident_log` に記録** (record_incident / resolve_incident)。
5. **本番を壊す操作は絶対にしない**。判断に迷ったら「メール報告のみ」に倒す。

## 点検項目 (毎回)

`src.db.connection.connect()` で Supabase を読み、以下を確認 (すべて読み取り):

| # | 点検 | 判定 | アクション |
|---|---|---|---|
| A | 本日のレース詳細ページ被覆 | `page_html_cache` の現行版 `race_detail_page:<ver>:<today>` が races の 50%未満 | **自動: tags→pages prewarm→top snapshot 再生成** |
| B | 本日/翌日のデータ準備 | races/entries/predictions が揃っているか | 揃っていて signals 未生成なら **自動: signals prewarm**。データ自体が無ければ **メール報告** (収集は cron の領分) |
| C | 前日結果の欠落 | 前日 race で results 欠落が一定数以上 (発走後) | **自動: poll_results --date 前日** |
| D | 未解決インシデント | `incident_log` の open 行 | 既知パターンで修復できたら resolve 記録。できなければ **メール報告** |
| E | cron 連続失敗 / プール枯渇多発 | task_runs failure 多発 / transient_db_error 多発 | 既知の自動修復対象でなければ **メール報告** |
| F | web 健全性 | `/healthz` が 200 か、revision | 異常なら **メール報告** |

## 手順 (擬似コード)

```
now = JST now
1. web /healthz を確認 (200/revision)。異常 → メール報告(重大)。
2. 点検A: 本日詳細キャッシュ被覆を数える。<50% なら:
     - prewarm_race_detail_tags --date today
     - prewarm_race_detail_pages --date today
     - build_top_page_snapshot --date today
     - 再点検して被覆を確認。直った→incident resolve記録。直らない→メール報告。
3. 点検B: today/tomorrow の races/predictions を確認。
     - データ有り & signals 未生成 → prewarm_strategy_pages --mode signals --date <d>
     - データ無し(準備前) → 時刻的に異常なら メール報告、そうでなければ静観。
4. 点検C: 前日 results 欠落を確認。発走後で欠落 → poll_results --date <yesterday>。
5. 点検D: incident_log open を確認。既知パターン(A/B/Cで直した)なら resolve記録。
     未知/コード要修正 → メール報告(内容添付)。
6. 実施した自動修復は incident_log に record/resolve で残す。
7. サマリ (点検結果 + 実施した自動修復 + 要承認事項) を、
   異常や自動修復があった時のみ 発注者へメール。平常時は静かに終了 (メールしない)。
```

## メール (report) の宛先・方法

- `src.notifications.mailer._send` / `cron_alerts.notify_cron_failure` の既存経路。
- 宛先は env `BOATRACE_ERROR_NOTIFY_TO`。
- **平常時 (異常なし・自動修復なし) はメールしない** (通知疲れ防止)。
- 自動修復した / 要承認事項がある / 重大異常のときだけメール。

## 冪等・多重起動対策

- prewarm は既存キャッシュをスキップするので重複実行は無害。
- 同一障害の連続修復を避けるため、incident_log の dedup と、
  self-heal の30分間隔下限を尊重。

## この Runbook の位置づけ

- 本番システム側の自動修復 (watchdog / self-heal / guardian) が**第一の防波堤**。
- このエージェントは**第二の防波堤 + 記録係 + 報告係**。
  「起きて、記録を見て、安全な範囲で直し、履歴を残し、必要なら報告する」。
