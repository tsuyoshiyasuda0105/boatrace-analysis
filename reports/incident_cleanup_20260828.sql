-- 解消済みインシデントを閉じる (2026-08-28 リン)
-- 実行場所: Supabase ダッシュボード → SQL Editor
--
-- 【なぜ今やるか】
-- 朝の点検 (preflight) に「未クローズのインシデントが 0 件であること」という
-- 項目がある。解消済みの古い件が残っていると毎朝この項目が落ち、その失敗が
-- また新しいインシデントを生む。閉じないと自己参照で永久に鳴り続ける。
--
-- 【残すもの】外部要因で現在進行中の 1 件のみ:
--   "repeated cron failures detected" / "program sources unresolved at 07:30 JST"
--   → boatrace-open-api.github.io が応答しない (外部サイトの障害)。
--     公式ダウンロードへの自動切替が働きデータは完全なので実害なし。
--     Open API が復旧したら閉じる。

-- 接続プールの枯渇・待ち行列に由来する一連の障害
-- (2026-08-24 待ち行列の上限撤廃 / 2026-08-26 プールの fork 共有を修正)
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = '待ち行列の上限撤廃 (2026-08-24) と、親プロセスのプールを全 worker が共有していた不具合の修正 (2026-08-26) で解消。以後の再発なし。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND (title LIKE '%pool-1%'
        OR title LIKE '%transient DB/pool failures%'
        OR title LIKE '%Exception on /member/today-races%');

-- バックテストのデルタ適用まわり
-- (2026-08-26 web の共有プールを経由せず直結接続にした)
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = 'デルタ適用を web の共有プールから切り離し直結接続にして解消 (2026-08-26)。取込漏れは無く、最新 8/27 まで反映済み。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND (title LIKE '%kachisuji delta apply%'
        OR title LIKE '%kachisuji startup delta apply%');

-- Supabase の接続枠 15 本を超えて cron が締め出された件
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = '接続予算を web 2x4 + cron 6 = 14 本に収めて解消 (2026-08-25)。枠超過の再発なし。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND title LIKE '%odds cron raised OperationalError%';

-- Value Bet API が本番構成で常時 500 だった件
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = '本番 web は予測モデル非搭載のため、この API は「未提供」を 200 で返すよう修正 (2026-08-25)。本提供は三連単確率の事前計算が前提。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND title LIKE '%value bet failed%';

-- 古い cron 失敗・結果取込の遅れ (いずれも現在は解消)
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = '当時の原因は解消済み。結果取込は 8/26・8/27 とも全レース完了を確認。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND (title LIKE '%regular cron completed with a failure status%'
        OR title LIKE '%yesterday result gaps remain after backfill%');

-- 朝の点検の警告 (自己参照で鳴っていた分)
UPDATE incident_log
   SET status = 'resolved', resolved_at = '2026-08-28T09:00:00', handled_by = 'lin',
       response_note = '2 件の失敗は (1) 取込中のロックを「データ無し」と誤読していた件 → 待つよう修正、(2) 未クローズ件数の自己参照 → 本 SQL で解消。',
       updated_at = '2026-08-28T09:00:00'
 WHERE status = 'open'
   AND title LIKE '%preflight warning%';

-- 確認: 残るのは Open API 停止に関する件のみになるはず
SELECT status, count(*) FROM incident_log GROUP BY status ORDER BY 2 DESC;
SELECT left(last_seen_at, 16) AS last_seen, occurrence_count, left(title, 70) AS title
  FROM incident_log WHERE status = 'open' ORDER BY last_seen_at DESC;
