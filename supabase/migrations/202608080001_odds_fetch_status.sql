CREATE TABLE IF NOT EXISTS public.odds_fetch_status (
    race_id text NOT NULL,
    snapshot_label text NOT NULL,
    state text NOT NULL,
    detail_code text NOT NULL,
    http_status integer,
    combination_count integer NOT NULL DEFAULT 0,
    retryable integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    checked_at text NOT NULL,
    last_success_at text,
    note text,
    PRIMARY KEY (race_id, snapshot_label)
);

CREATE INDEX IF NOT EXISTS idx_odds_fetch_status_state
    ON public.odds_fetch_status(state, checked_at);

ALTER TABLE public.odds_fetch_status ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.odds_fetch_status IS
    'Latest per-race odds fetch state for fetched / missing / retry_waiting monitoring.';
