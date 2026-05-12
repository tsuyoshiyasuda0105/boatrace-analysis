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
