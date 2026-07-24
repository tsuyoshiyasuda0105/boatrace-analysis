CREATE TABLE IF NOT EXISTS motor_preinspection_stats (
  stadium_number integer NOT NULL,
  race_date date NOT NULL,
  source_name text NOT NULL,
  racer_number integer,
  racer_name text,
  racer_class text,
  motor_number integer NOT NULL,
  motor_win2_rate double precision,
  boat_number integer,
  boat_win2_rate double precision,
  preinspection_time double precision,
  preinspection_rank integer,
  raw_text text,
  source_url text,
  collected_at timestamptz,
  PRIMARY KEY (stadium_number, race_date, source_name, motor_number, racer_number)
);

CREATE INDEX IF NOT EXISTS idx_motor_preinspection_lookup
  ON motor_preinspection_stats(stadium_number, motor_number, race_date DESC);

CREATE INDEX IF NOT EXISTS idx_motor_preinspection_racer
  ON motor_preinspection_stats(stadium_number, race_date, racer_number);

ALTER TABLE motor_preinspection_stats ENABLE ROW LEVEL SECURITY;
