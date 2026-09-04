-- public スキーマで RLS が無効な 18 テーブルに RLS を有効化する
-- 作成: 2026-08-24 (リン)
-- 実行場所: Supabase ダッシュボード → SQL Editor
--
-- 【なぜ安全か】
-- アプリは DATABASE_URL の postgres ロールで接続しており、このロールは
-- rolbypassrls = true (RLS を迂回できる)。したがって RLS を有効にしても
-- アプリの読み書きは一切変わらない。確認コマンド:
--     SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user;  -- → t
--
-- 【何を防ぐか】
-- Supabase は public スキーマを REST API (PostgREST) で公開しており、anon キーを
-- 持つ相手は RLS の無いテーブルを自由に読める。現時点で anon キーをブラウザに
-- 出してはいない (テンプレートにも JS にも埋め込みなし) ので緊急事態ではないが、
-- anon キーは本来「公開される前提」の鍵で、守りは RLS 側で行う設計になっている。
-- ポリシーを 1 つも作らずに RLS を有効化すると「既定で全拒否」になる。
--
-- 【対象に含めた理由 (特に守りたいもの)】
--   page_html_cache          … 生成済みページHTML。会員向け画面が含まれうる
--   kachisuji_delta_files    … バックテストの配信データ (商品そのもの)
--   racer_accident_*         … 事故率まわりの分析データ (商品の中核)
--   derived_start_stats      … ST 派生統計
--   incident_log             … 障害の詳細。内部パスやトレースを含む
--   x_post_queue             … 投稿キュー

ALTER TABLE public.derived_start_stats                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.edogawa_motor_cyusen                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.incident_log                        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kachisuji_delta_files               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.page_html_cache                     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_original_exhibitions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_program_tags                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.race_tides                          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_events               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_external_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_kraw_unmatched       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_period_adjustments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_period_stats         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_point_rules          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_accident_rank_snapshots       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.racer_entry_change_snapshots        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_rate_slots                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.x_post_queue                        ENABLE ROW LEVEL SECURITY;

-- 確認: 残りがゼロになること
SELECT tablename
  FROM pg_tables
 WHERE schemaname = 'public' AND NOT rowsecurity
 ORDER BY tablename;
