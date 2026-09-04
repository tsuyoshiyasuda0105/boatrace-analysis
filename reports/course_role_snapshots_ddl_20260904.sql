-- racer_course_role_snapshots の仕上げ (型の修正 + RLS)
-- 作成: 2026-09-04 (リン)
-- 実行場所: Supabase ダッシュボード → SQL Editor
-- 所要: 数秒 (578行の小さな表)
-- 前提: build_racer_course_role_stats.py が本番でテーブル作成済み
--
-- ============================================================
-- 1) 率カラムを real → double precision に変更する
-- ============================================================
-- real は Postgres では 4 バイト。42/60 や 26/40 の「ちょうど 0.70 / 0.65」が
-- 0.6999999... に丸められる。閾値がその値ちょうどなので、SQL で数えると
-- 該当が 4 名少なく出る (逃げ 112→110 / 壁 71→69)。アプリは Python 側で
-- 比較しており表示は正しいが、後からこの表を SQL で集計した人だけが違う
-- 答えを得る状態になる。それを塞ぐ。
--
-- 注意: 型を広げても、すでに丸められた値は元に戻らない。この SQL のあとに
-- リンが集計スクリプトを再実行して値を書き直す。

ALTER TABLE public.racer_course_role_snapshots
  ALTER COLUMN course1_win_rate     TYPE DOUBLE PRECISION;

ALTER TABLE public.racer_course_role_snapshots
  ALTER COLUMN course2_nigashi_rate TYPE DOUBLE PRECISION;

-- ============================================================
-- 2) RLS を有効化する
-- ============================================================
-- 2026-08-24 に public スキーマの 18 テーブルへ RLS を入れた
-- (reports/enable_rls_public_tables_20260824.sql)。この表はその後に生まれた
-- ため、放置すると「RLS の無い唯一の公開テーブル」になる。PostgREST は
-- public スキーマを公開しており、anon キーを持つ相手は RLS の無いテーブルを
-- 自由に読める。中身は各選手の 1 コース逃げ率 / 2 コース逃がし率 =
-- タグ判定ロジックそのものなので、兄弟テーブル
-- (racer_entry_change_snapshots / racer_accident_rank_snapshots) と揃える。
--
-- アプリは DATABASE_URL の postgres ロールで接続しており rolbypassrls = true。
-- RLS を有効にしてもアプリの読み書きは一切変わらない。
-- ポリシーを 1 つも作らないので anon からは既定で全拒否になる。

ALTER TABLE public.racer_course_role_snapshots ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 3) 確認 (この 3 行が期待どおりか見てください)
-- ============================================================
-- 期待: course1_win_rate / course2_nigashi_rate がどちらも
--       double precision、rowsecurity = true
SELECT c.column_name,
       c.data_type,
       t.relrowsecurity AS rls_enabled
  FROM information_schema.columns c
  JOIN pg_class t ON t.relname = c.table_name
 WHERE c.table_name = 'racer_course_role_snapshots'
   AND c.column_name IN ('course1_win_rate', 'course2_nigashi_rate')
 ORDER BY c.column_name;
