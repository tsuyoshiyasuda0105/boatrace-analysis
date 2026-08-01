CREATE TABLE IF NOT EXISTS public.roi_race_history (
    race_date text NOT NULL,
    race_id text NOT NULL,
    strategy_key text NOT NULL,
    strategy_label text,
    bet_json text NOT NULL,
    stake_amount integer NOT NULL,
    payout_amount integer NOT NULL DEFAULT 0,
    is_hit integer NOT NULL DEFAULT 0,
    is_settled integer NOT NULL DEFAULT 0,
    is_active integer NOT NULL DEFAULT 1,
    source_cache_key text NOT NULL,
    source_cache_version text,
    strategy_signature text,
    snapshot_computed_at text,
    capture_quality text NOT NULL,
    payload_hash text NOT NULL,
    updated_at text NOT NULL,
    PRIMARY KEY (race_id, strategy_key)
);

CREATE INDEX IF NOT EXISTS idx_roi_race_history_date
    ON public.roi_race_history(race_date);
CREATE INDEX IF NOT EXISTS idx_roi_race_history_strategy_date
    ON public.roi_race_history(strategy_key, race_date);

ALTER TABLE public.roi_race_history ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.roi_race_history IS
    'Durable per-race ledger of high-ROI races selected by saved market-signals snapshots.';
