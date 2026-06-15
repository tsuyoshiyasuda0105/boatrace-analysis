-- ============================================================
-- BOATRACE データ分析プロジェクト - DBスキーマ
-- ------------------------------------------------------------
-- 設計方針:
--   - 「いつ取得可能か」を意識して、テーブルを目的別に分離
--   - races        : 前日確定 (Layer1番組表 / Layer2 Open API Programs)
--   - race_entries : 前日確定 (出走表の艇単位データ)
--   - race_previews: 直前確定 (Layer2 Open API Previews)
--   - race_results : 結果確定 (Layer1競走成績 / Layer2 Open API Results)
--   - race_parts   : 直前確定 (Layer3 公式サイトスクレイピング)
--   - odds_*       : 締切前後 (オプション)
--   - racers / motors / boats : マスタ
-- ============================================================

-- ============================================================
-- マスタ系
-- ============================================================

CREATE TABLE IF NOT EXISTS stadiums (
  stadium_number INTEGER PRIMARY KEY,
  name           TEXT    NOT NULL,
  water          TEXT    NOT NULL,    -- fresh/brackish/sea
  is_night       INTEGER NOT NULL,    -- 0/1 (SQLiteのBOOLEAN代替)
  in_strength    TEXT    NOT NULL,    -- low/mid/high/very_high
  tide_effect    TEXT    NOT NULL,    -- none/mid/high
  altitude_high  INTEGER NOT NULL DEFAULT 0,
  notes          TEXT
);

CREATE TABLE IF NOT EXISTS racers (
  -- ファン手帳（半期更新）+ 番組表で構築する選手マスタ
  racer_number       INTEGER PRIMARY KEY,
  name               TEXT,
  name_kana          TEXT,
  branch_number      INTEGER,
  birthplace_number  INTEGER,
  birth_date         TEXT,
  gender             INTEGER,         -- 1:男 2:女
  height_cm          INTEGER,
  blood_type         TEXT,
  registered_period  INTEGER,         -- 養成期
  updated_at         TEXT NOT NULL    -- このレコード更新日 (YYYY-MM-DD)
);

CREATE TABLE IF NOT EXISTS racer_period_stats (
  -- ファン手帳: 半期ごとの選手成績
  racer_number                    INTEGER NOT NULL,
  period_year                     INTEGER NOT NULL,    -- 例 2025
  period_half                     INTEGER NOT NULL,    -- 1:前期 2:後期
  class_number                    INTEGER,             -- 1:A1 2:A2 3:B1 4:B2
  win_rate                        REAL,
  place_rate                      REAL,
  first_count                     INTEGER,
  second_count                    INTEGER,
  start_count                     INTEGER,
  ability_index                   REAL,
  avg_start_timing                REAL,
  course_1_entries                INTEGER,
  course_1_place_rate             REAL,
  course_1_avg_st                 REAL,
  course_2_entries                INTEGER,
  course_2_place_rate             REAL,
  course_2_avg_st                 REAL,
  course_3_entries                INTEGER,
  course_3_place_rate             REAL,
  course_3_avg_st                 REAL,
  course_4_entries                INTEGER,
  course_4_place_rate             REAL,
  course_4_avg_st                 REAL,
  course_5_entries                INTEGER,
  course_5_place_rate             REAL,
  course_5_avg_st                 REAL,
  course_6_entries                INTEGER,
  course_6_place_rate             REAL,
  course_6_avg_st                 REAL,
  PRIMARY KEY (racer_number, period_year, period_half)
);

-- ============================================================
-- レース系
-- ============================================================

CREATE TABLE IF NOT EXISTS races (
  -- 1レース = 1行。番組表 / Open API Programs ヘッダ部分
  race_id           TEXT PRIMARY KEY,            -- 'YYYYMMDD-SS-RR' 形式 (例: 20260508-04-01)
  race_date         TEXT NOT NULL,
  stadium_number    INTEGER NOT NULL,
  race_number       INTEGER NOT NULL,
  race_grade_number INTEGER,                     -- 1:SG 2:G1 3:G2 4:G3 5:一般 等
  race_title        TEXT,
  race_subtitle     TEXT,
  race_distance     INTEGER,                     -- 通常 1800m
  race_closed_at    TEXT,                        -- 締切時刻 (datetime)
  series_day        INTEGER,                     -- 節何日目か (1-7)。後処理で算出
  is_yusho          INTEGER DEFAULT 0,           -- 優勝戦フラグ
  is_jun_yusho      INTEGER DEFAULT 0,           -- 準優勝戦フラグ
  FOREIGN KEY (stadium_number) REFERENCES stadiums(stadium_number)
);
CREATE INDEX IF NOT EXISTS idx_races_date ON races(race_date);
CREATE INDEX IF NOT EXISTS idx_races_stadium_date ON races(stadium_number, race_date);

CREATE TABLE IF NOT EXISTS race_entries (
  -- 1レース×6艇の出走表データ。前日確定。
  race_id                              TEXT NOT NULL,
  boat_number                          INTEGER NOT NULL,    -- 1-6
  racer_number                         INTEGER NOT NULL,
  racer_name                           TEXT,
  class_number                         INTEGER,             -- 1:A1 ...
  branch_number                        INTEGER,
  birthplace_number                    INTEGER,
  age                                  INTEGER,
  weight                               REAL,
  flying_count                         INTEGER,             -- F (フライング) 回数
  late_count                           INTEGER,             -- L (出遅れ) 回数
  avg_start_timing                     REAL,
  national_top_1_percent               REAL,
  national_top_2_percent               REAL,
  national_top_3_percent               REAL,
  local_top_1_percent                  REAL,
  local_top_2_percent                  REAL,
  local_top_3_percent                  REAL,
  assigned_motor_number                INTEGER,
  assigned_motor_top_2_percent         REAL,
  assigned_motor_top_3_percent         REAL,
  assigned_boat_number                 INTEGER,
  assigned_boat_top_2_percent          REAL,
  assigned_boat_top_3_percent          REAL,
  PRIMARY KEY (race_id, boat_number),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
CREATE INDEX IF NOT EXISTS idx_entries_racer ON race_entries(racer_number);
CREATE INDEX IF NOT EXISTS idx_entries_motor ON race_entries(assigned_motor_number);

CREATE TABLE IF NOT EXISTS race_previews (
  -- 直前情報。レース開始15-30分前に確定。1レース×6艇。
  race_id                  TEXT NOT NULL,
  boat_number              INTEGER NOT NULL,
  -- レース全体の値 (race_id内で同じだが正規化せず冗長保持。クエリ容易)
  weather_number           INTEGER,
  wind_speed               INTEGER,             -- m/s
  wind_direction_number    INTEGER,
  wave_height              INTEGER,             -- cm
  temperature              REAL,
  water_temperature        REAL,
  stable_plate             INTEGER,             -- 0/1: 安定板使用
  -- 艇単位
  course_number            INTEGER,             -- 進入コース (枠なり崩れ検出に重要)
  exhibition_time          REAL,                -- 展示タイム
  start_timing_exhibition  REAL,                -- 展示ST
  weight_adjustment        REAL,                -- 体重調整 (斤量)
  tilt_adjustment          REAL,                -- チルト
  PRIMARY KEY (race_id, boat_number),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE TABLE IF NOT EXISTS race_parts (
  -- 部品交換 / プロペラ交換情報。Layer 3 スクレイピング由来。
  -- 1艇複数部品交換可。1行=1部品。
  race_id      TEXT NOT NULL,
  boat_number  INTEGER NOT NULL,
  part_code    TEXT NOT NULL,         -- piston/ring/electric/carb/cylinder/shaft/gear/carrier/propeller
  PRIMARY KEY (race_id, boat_number, part_code),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE TABLE IF NOT EXISTS race_results (
  -- レース結果。1レース×6艇。
  race_id              TEXT NOT NULL,
  boat_number          INTEGER NOT NULL,
  finishing_position   INTEGER,             -- 1-6 (失格は NULL)
  course_number        INTEGER,
  start_timing         REAL,
  race_time            TEXT,                -- 例 '1.50.3' (1分50秒3)
  remarks              TEXT,                -- F, L0, L1, K0, K1, S0, S1, S2, 失格等
  kimarite             TEXT,                -- 決まり手 (1着のみ): 逃げ/差し/まくり/まくり差し/抜き/恵まれ
  PRIMARY KEY (race_id, boat_number),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
-- ROI 集計 SQL の "WHERE finishing_position=N" JOIN を高速化
CREATE INDEX IF NOT EXISTS idx_results_race_pos
  ON race_results(race_id, finishing_position);

CREATE TABLE IF NOT EXISTS race_payouts (
  -- 払戻金。三連単/三連複/二連単/二連複/拡連複/単勝/複勝
  race_id     TEXT NOT NULL,
  bet_type    TEXT NOT NULL,        -- 'trifecta'/'trio'/'exacta'/'quinella'/'quinella_place'/'win'/'place'
  combination TEXT NOT NULL,        -- '1-2-3' / '1=2=3' / '1' 等
  payout      INTEGER NOT NULL,     -- 円
  popularity  INTEGER,              -- 人気順位
  PRIMARY KEY (race_id, bet_type, combination),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
-- PRIMARY KEY だけでは "WHERE bet_type='trifecta' GROUP BY race_id" のサブクエリが
-- 全行スキャンになりがち。bet_type で先に絞り込めるよう補助 index
CREATE INDEX IF NOT EXISTS idx_payouts_type_combination
  ON race_payouts(bet_type, combination, race_id);

-- ============================================================
-- オッズ (オプション。スクレイピングで取得する場合)
-- ============================================================

CREATE TABLE IF NOT EXISTS odds_trifecta (
  -- 3連単オッズ。120通り×レース数なので肥大化注意。
  race_id        TEXT NOT NULL,
  combination    TEXT NOT NULL,        -- '1-2-3' 形式
  odds           REAL NOT NULL,
  is_final       INTEGER NOT NULL,     -- 1=確定オッズ, 0=途中
  recorded_at    TEXT NOT NULL,        -- 取得日時
  snapshot_label TEXT,                 -- T-15min / T-5min / T-1min / final など
  PRIMARY KEY (race_id, combination, recorded_at),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
-- ROI 集計 SQL の "WHERE combination='1-2-3' AND snapshot_label IN (...)" を高速化
CREATE INDEX IF NOT EXISTS idx_odds_combo_snap
  ON odds_trifecta(combination, snapshot_label, race_id);

-- ============================================================
-- 予測 / バックテスト
-- ============================================================

CREATE TABLE IF NOT EXISTS predictions (
  -- モデル予測結果。同じレースに複数モデル/バージョンの予測があり得る。
  race_id          TEXT NOT NULL,
  boat_number      INTEGER NOT NULL,
  model_version    TEXT NOT NULL,
  prob_first       REAL,             -- 1着確率
  prob_top_2       REAL,             -- 2着以内確率
  prob_top_3       REAL,             -- 3着以内確率
  predicted_at     TEXT NOT NULL,
  PRIMARY KEY (race_id, boat_number, model_version),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
-- ROI 集計 SQL の "WHERE boat_number=1" JOIN を高速化
CREATE INDEX IF NOT EXISTS idx_predictions_boat
  ON predictions(boat_number, race_id);

CREATE TABLE IF NOT EXISTS value_bets (
  -- 期待値プラス検出ログ
  race_id          TEXT NOT NULL,
  bet_type         TEXT NOT NULL,
  combination      TEXT NOT NULL,
  predicted_prob   REAL NOT NULL,
  market_odds      REAL NOT NULL,
  expected_value   REAL NOT NULL,    -- (prob × odds) - 1
  kelly_fraction   REAL,             -- 推奨ベットサイズ (1/4 Kelly等)
  model_version    TEXT NOT NULL,
  detected_at      TEXT NOT NULL,
  -- 結果検証用 (後埋め)
  actual_hit       INTEGER,          -- 0/1
  actual_payout    INTEGER,          -- 円 (100円賭けた場合の払戻)
  PRIMARY KEY (race_id, bet_type, combination, model_version),
  FOREIGN KEY (race_id) REFERENCES races(race_id)
);
CREATE INDEX IF NOT EXISTS idx_value_bets_ev ON value_bets(expected_value DESC);

-- ============================================================
-- メール通知機能 (アラート購読者)
-- ============================================================

CREATE TABLE IF NOT EXISTS alert_subscribers (
  -- メールアドレス購読者
  -- セキュリティ: 平文メアドは保存しない、AES-GCM 暗号化保存
  email_hash         TEXT PRIMARY KEY,   -- SHA-256 ハッシュ (重複登録防止)
  email_encrypted    TEXT NOT NULL,      -- AES-GCM 暗号化されたメアド (送信時のみ複号)
  alert_types        TEXT NOT NULL DEFAULT '["L4_SG","L4_G1","L4_G2"]',  -- JSON 配列
  min_recovery_rate  REAL NOT NULL DEFAULT 150.0,  -- 通知する最小回収率閾値 (%)
  is_active          INTEGER NOT NULL DEFAULT 1,
  is_verified        INTEGER NOT NULL DEFAULT 0,    -- 確認メール認証済か
  verification_token TEXT,                          -- 確認メール用 (有効期限付き)
  verification_expires_at TEXT,
  unsubscribe_token  TEXT,                          -- ワンクリック解除用
  created_at         TEXT NOT NULL,
  last_notified_at   TEXT,
  notify_count       INTEGER NOT NULL DEFAULT 0,
  ip_at_signup       TEXT                            -- 不正登録対策 (ハッシュ化)
);
CREATE INDEX IF NOT EXISTS idx_alert_sub_active ON alert_subscribers(is_active, is_verified);

CREATE TABLE IF NOT EXISTS alert_sent (
  -- 送信履歴 (重複送信防止)
  email_hash  TEXT NOT NULL,
  race_id     TEXT NOT NULL,
  alert_type  TEXT NOT NULL,
  sent_at     TEXT NOT NULL,
  PRIMARY KEY (email_hash, race_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_alert_sent_at ON alert_sent(sent_at);

-- ============================================================
-- L4 [A1] ROI 日別集計 (Supabase 容量節約のための precompute テーブル)
-- ------------------------------------------------------------
-- 過去日について生データ (races / race_entries / race_payouts / race_results)
-- を Supabase に置かず、日別 ROI 集計値のみを保持する。
-- ダッシュボード (`src/web/app.py:_l4_daily_stats`) が by_date 補完に参照。
-- ------------------------------------------------------------
-- カラム命名:
--   tri_*       : L4 [A1] 3連単 1-2-3 集計 (grade IN 1,2,3,4)
--   c80_*       : 1コース 1着率 80%+ 派生
--   pro_*       : L4 PRO (avg_st<0.16 & age 30-49 & ex_st<0.18)
--   sgg12_*     : 高グレード SG/G1/G2 派生
--   gen_tri_*       : 一般戦 (grade=5) × A1 × B除外 × 本命500-1000 観察集計
--   gen_plus_tri_*  : 一般戦 × 国1%≥7 サブセット (L4+ オーバーレイ重畳)
-- ============================================================

CREATE TABLE IF NOT EXISTS l4_daily_summary (
  date              TEXT PRIMARY KEY,
  n_total           INTEGER,        -- 当日全レース数
  n_l4              INTEGER,        -- L4 [A1] 該当レース数
  win_bets          INTEGER, win_hits  INTEGER, win_pay  INTEGER,
  exa_bets          INTEGER, exa_hits  INTEGER, exa_pay  INTEGER,
  tri_bets          INTEGER, tri_hits  INTEGER, tri_pay  INTEGER,
  c80_bets          INTEGER, c80_hits  INTEGER, c80_pay  INTEGER,
  pro_bets          INTEGER, pro_hits  INTEGER, pro_pay  INTEGER,
  sgg12_bets        INTEGER, sgg12_hits INTEGER, sgg12_pay INTEGER,
  -- 一般戦 (grade=5) 観察集計 (Phase 1: ROI ダッシュボード観察のみ、本日候補/メールは現状維持)
  gen_tri_bets      INTEGER,
  gen_tri_hits      INTEGER,
  gen_tri_pay       INTEGER,
  -- 一般戦 × 国1%≥7 (= L4+ オーバーレイ) 重畳サブセット (観察用)
  gen_plus_tri_bets INTEGER,
  gen_plus_tri_hits INTEGER,
  gen_plus_tri_pay  INTEGER,
  -- 一般戦 F1 採用ベース: 一般戦 × 国1%≥7 × 2号 国2連率≥40
  -- OOS Tier 1 (4年 ROI 204% / CI 下限 ≥150%) → 本日候補/メール対象
  gen_f1_tri_bets   INTEGER,
  gen_f1_tri_hits   INTEGER,
  gen_f1_tri_pay    INTEGER,
  -- L4-prime 観察集計 (11R-12R 限定、全グレード、ROI 185% 検証)
  -- 3 ヶ月実績で採用判断する観察ベース
  prime_tri_bets    INTEGER,
  prime_tri_hits    INTEGER,
  prime_tri_pay     INTEGER,
  -- L4-12R-only 観察集計 (12R のみ、全グレード、ROI 193% 検証)
  r12_tri_bets      INTEGER,
  r12_tri_hits      INTEGER,
  r12_tri_pay       INTEGER,
  -- 一般戦×12R 観察集計 (一般戦の 12R 限定、ROI 189% 検証)
  gen_r12_tri_bets  INTEGER,
  gen_r12_tri_hits  INTEGER,
  gen_r12_tri_pay   INTEGER,
  -- 戸田 7R 企画レース観察 (B除外なのに 7R 限定で +EV、ROI 171.5% 検証 n=106)
  -- 3 ヶ月実績で採用判断する観察ベース (2026-05-19 追加)
  toda_7r_tri_bets  INTEGER,
  toda_7r_tri_hits  INTEGER,
  toda_7r_tri_pay   INTEGER,
  -- L4-Mid + 1-3-2 観察 (オッズ 10-20倍帯で 1-3-2 単点、検証 ROI 148.1% n=10690)
  -- 2026-05-19 追加。L4 帯と異なる universe、1号艇1着率93%+で 1-3-2 が最頻出
  mid_132_tri_bets  INTEGER,
  mid_132_tri_hits  INTEGER,
  mid_132_tri_pay   INTEGER,
  -- L4-Mid Tier A (上記 + 3号艇国1% ≥ 7%): ROI 175.5% n=1312 CI[151,200] Tier 1認定
  -- 2026-05-19 追加。3号艇が中堅A1+の時に絞り、より高 ROI で観察
  mid_132_tier_a_tri_bets INTEGER,
  mid_132_tier_a_tri_hits INTEGER,
  mid_132_tier_a_tri_pay  INTEGER,
  updated_at        TEXT NOT NULL
);

-- 既存 DB へ手動適用する場合 (一般戦観察カラム追加):
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_tri_bets      INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_tri_hits      INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_tri_pay       INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_plus_tri_bets INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_plus_tri_hits INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_plus_tri_pay  INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_f1_tri_bets   INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_f1_tri_hits   INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_f1_tri_pay    INTEGER;

-- 既存 DB へ手動適用する場合 (L4-prime/12R 観察カラム追加):
-- ALTER TABLE l4_daily_summary ADD COLUMN prime_tri_bets    INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN prime_tri_hits    INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN prime_tri_pay     INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN r12_tri_bets      INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN r12_tri_hits      INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN r12_tri_pay       INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_r12_tri_bets  INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_r12_tri_hits  INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN gen_r12_tri_pay   INTEGER;

-- 既存 DB へ手動適用する場合 (戸田7R 企画レース観察カラム追加 2026-05-19):
-- ALTER TABLE l4_daily_summary ADD COLUMN toda_7r_tri_bets  INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN toda_7r_tri_hits  INTEGER;
-- ALTER TABLE l4_daily_summary ADD COLUMN toda_7r_tri_pay   INTEGER;

-- ============================================================
-- システム状態 (データ品質チェック / バッチ死活監視 / エラーログ)
-- backlog item 3: 朝のバッチ後に整合性チェック → Web 上で warning 表示
-- ============================================================
CREATE TABLE IF NOT EXISTS system_status (
  check_name   TEXT NOT NULL,     -- 'morning_data_complete' / 'races_count' / 'predictions_count' 等
  check_date   TEXT NOT NULL,     -- YYYY-MM-DD
  status       TEXT NOT NULL,     -- 'ok' / 'warning' / 'error'
  message      TEXT,              -- 人間向けメッセージ ('尼崎 R9-12 選手情報待ち' 等)
  detail_json  TEXT,              -- 詳細データ (JSON)
  checked_at   TEXT NOT NULL,     -- ISO 8601
  PRIMARY KEY (check_name, check_date)
);
CREATE INDEX IF NOT EXISTS idx_sysstat_date ON system_status(check_date, status);

-- ============================================================
-- タスク実行ログ (起動時キャッチアップ用)
-- サーバー(ローカルPC)がスケジュール時刻にダウンしていてタスクが実行され
-- なかった場合に、起動時 (scripts/startup_catchup.py) が「今日そのタスクが
-- 成功したか」を判定するための実行記録。判定はローカルPCの状態に基づくため
-- 常にローカル SQLite に書く (src/db/task_log.py が sqlite3 直書きで担保)。
--   task_name : 'daily_collect' / 'morning' / 'hourly' / 'poll_results'
--   status    : 'success' / 'failure' / 'running'
--   trigger   : 'scheduled' (通常実行) / 'catchup' (起動時キャッチアップ)
-- ============================================================
CREATE TABLE IF NOT EXISTS task_runs (
  task_name   TEXT NOT NULL,
  run_date    TEXT NOT NULL,     -- YYYY-MM-DD (ローカル日付)
  status      TEXT NOT NULL,     -- 'success' / 'failure' / 'running'
  run_count   INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT,              -- ISO 8601 (最後の開始)
  finished_at TEXT,              -- ISO 8601 (最後の終了)
  success_at  TEXT,              -- ISO 8601 (最後に成功した時刻。当日未成功なら NULL)
  trigger     TEXT,              -- 'scheduled' / 'catchup'
  detail      TEXT,
  PRIMARY KEY (task_name, run_date)
);
