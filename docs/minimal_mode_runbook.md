# 最小構成への切り替え手順（2026-10 作成）

お客さんがいない間、月の費用を約59.5ドル → 約8.5ドルに下げるための手順。
**バックテスト・無料登録・特典ボタン・出走表と朝の予想は残す。当日のリアルタイム情報を止める。**

## 前提（済んでいること）
- 本番DBの控え: `data/prod_backup/2026-10-07/`（59表・行数一致・戻す道具あり。`data/prod_backup/README.md`）
- コード（main に入っていれば、設定を入れるまでは今までどおり動く）:
  - バックテスト検索の同時実行数の上限 `KACHISUJI_SEARCH_CONCURRENCY`（0/未設定=制限なし）
  - サイト上部のお知らせ `BOATRACE_SITE_NOTICE`（空=出さない）
  - PC夜間処理が前日のレース結果・払戻を本番へ送る（日中の結果取得を止めても翌朝に入る）

## 止めるもの・残すもの（Render）
| サービス | 役目 | 最小構成 |
|---|---|---|
| boatrace-web | サイト本体・バックテスト | **残す**（Starter へ） |
| boatrace-program-bootstrap-cron | 番組表と朝の予想（夜〜朝） | **残す** |
| boatrace-race-detail-cron | 早朝の整備・バックテストの毎朝の取り込み | **残す** |
| boatrace-regular-cron | 日中の直前情報・オリジナル展示・結果 | 一時停止 |
| boatrace-odds-cron | 締切5分前オッズ | 一時停止 |
| boatrace-exhibition-detail-cron | 展示後のレース詳細とタグ | 一時停止 |
| boatrace-accident-external-check-cron | 朝の事故データ点検 | 一時停止 |

## 当日の手順（2026-10-10 に前倒しで実施）
※スレッドは 2（「スレッド数×2 ≦ DB接続数4」の決まり。tests/test_db_pool_warmth.py が検算）。
1. サイトのお知らせ文を決める（下書きは下）。LINE・X の文はリッキーさんが送る。
2. Render の web（boatrace-web）の Environment に追加（**リッキーさんが押す・保存で再起動**）:
   - `KACHISUJI_SEARCH_CONCURRENCY` = `1`
   - `BOATRACE_SITE_NOTICE` = お知らせ文
3. web の Start Command を 1 台に（メモリ 512MB に収めるため）:
   `gunicorn -w 1 --worker-class gthread --threads 2 -b 0.0.0.0:$PORT --timeout 120 --graceful-timeout 30 'src.web.app:create_app(cached_predictions_only=True)'`
4. web の Instance Type を **Starter** に（料金の操作なのでリッキーさん）。
5. 上の表の「一時停止」4本を Render の各サービス画面で **Suspend**（Resume で戻る）。
6. render.yaml も同じ内容に直してコミット（plan: starter・startCommand）。**Blueprint の自動同期が有効だと、画面で変えた値が render.yaml で戻される**ので、2〜4 と同じ日に揃える。
7. 1週間、毎朝確認: Render の Metrics（メモリ）・エラーの有無・バックテストの最新日（毎朝更新されるか）・トップの出走表。
   メモリが足りずに落ちるなら、web だけ Standard に戻す。

## 11月1〜5日（Supabase の次の請求 11/7 の前）
8. 本番DBを直近2週間分に減らす（0.5GB 以下へ）。**消す表と件数を見せて確認をもらってから。** 先に控えを取り直す（`backup_prod.py`）。
9. Supabase を Free に（リッキーさんが押す）。Free は1週間アクセスが無いと止まる・毎日のバックアップ無し。

## 元に戻す
- cron: 各サービスで Resume。web: Instance Type を Standard、Start Command を `-w 2 --threads 2` に、環境変数2つを削除。
- データ: `restore_prod.py data/prod_backup/<日付> --tables <表> --apply`（足りない行だけ戻る）。
- 戻す目安: 有料会員が出たとき、または無料登録が月20人を超えたとき。

## お知らせ文（下書き・前向き版）
【お知らせ】データ検証に集中するため、当日の直前情報（展示タイム・オッズなど）の自動更新を一時お休みしています。出走表・朝の予想・バックテストはこれまでどおりご利用いただけます。直前情報はボートレース公式サイトの「直前情報」をご確認ください。
