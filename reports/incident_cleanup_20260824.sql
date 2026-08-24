-- 未クローズのインシデントのうち、原因が確実に解消したものを閉じる
-- 作成: 2026-08-24 (リン)
-- 実行場所: Supabase ダッシュボード → SQL Editor
--
-- 【残すもの】接続枠 (Supabase pooler の 15 本上限) にまつわる件は、
-- 対策の効果を明朝の稼働で確認してから閉じる。今日の時点では未確定。
--   - "the pool 'pool-1' has already 12 requests waiting"
--   - "transient DB/pool failures are recurring"
--   - "odds cron raised OperationalError"
--   - "kachisuji startup delta apply failed" / "kachisuji delta apply failed"
--   - "repeated cron failures detected"
--   - "yesterday result gaps remain after backfill"
--   - "regular cron completed with a failure status"
--   - "preflight warning"

UPDATE incident_log
   SET status        = 'resolved',
       resolved_at   = '2026-08-24T22:10:00',
       handled_by    = 'lin',
       response_note = 'cron 起動時のシークレット検証を修正済み (2026-08-20)。以後の再発なし。',
       updated_at    = '2026-08-24T22:10:00'
 WHERE status = 'open'
   AND title LIKE '%SECURITY: WEB_SESSION_SECR%';

UPDATE incident_log
   SET status        = 'resolved',
       resolved_at   = '2026-08-24T22:10:00',
       handled_by    = 'lin',
       response_note = 'エラーハンドラの動作確認用に意図的に発生させた 500。障害ではない。',
       updated_at    = '2026-08-24T22:10:00'
 WHERE status = 'open'
   AND title LIKE '%/_forced-500%';

UPDATE incident_log
   SET status        = 'resolved',
       resolved_at   = '2026-08-24T22:10:00',
       handled_by    = 'lin',
       response_note = 'メンテ窓 (04-07時) が内部処理まで 503 に差し替えていた問題を f4588ba で恒久修正。内部 API をメンテ窓の対象外にした。',
       updated_at    = '2026-08-24T22:10:00'
 WHERE status = 'open'
   AND (title LIKE '%maintenance window ended degraded%'
        OR title LIKE '%kachisuji delta apply trigger failed%');

UPDATE incident_log
   SET status        = 'resolved',
       resolved_at   = '2026-08-24T22:10:00',
       handled_by    = 'lin',
       response_note = '前夜 Layer 1 取り込みの順序を修正済み。以後の再発なし。',
       updated_at    = '2026-08-24T22:10:00'
 WHERE status = 'open'
   AND title LIKE '%program sources unresolved at 07:30%';

-- 確認用
SELECT status, count(*) FROM incident_log GROUP BY status ORDER BY 2 DESC;
