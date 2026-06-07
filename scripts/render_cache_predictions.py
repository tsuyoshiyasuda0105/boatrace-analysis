from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect
from src.web.predictor import Predictor


def _require_postgres() -> None:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be set to Supabase/Postgres on Render")


def cache_predictions_for_date(target_date: str, version: str = "v0.8") -> int:
    _require_postgres()
    predictor = Predictor(version=version)
    predictor.load()
    df = predictor.predict_date(target_date, force=True)
    if df is None or df.empty:
        print(f"[{target_date}] no prediction rows")
        return 0

    now_iso = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            row["race_id"],
            int(row["boat_number"]),
            version,
            float(row.get("prob_first", 0) or 0),
            float(row.get("prob_top_2", 0) or 0),
            float(row.get("prob_top_3", 0) or 0),
            now_iso,
        )
        for _, row in df.iterrows()
    ]

    with db_connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO predictions
            (race_id, boat_number, model_version, prob_first, prob_top_2, prob_top_3, predicted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    n_races = df["race_id"].nunique()
    print(f"[{target_date}] cached {n_races} races / {len(rows)} rows to Supabase")
    return int(n_races)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--tomorrow", action="store_true")
    parser.add_argument("--version", default="v0.8")
    args = parser.parse_args()

    if args.tomorrow:
        targets = [(date.today() + timedelta(days=1)).isoformat()]
    elif args.today:
        targets = [date.today().isoformat()]
    elif args.date:
        targets = [args.date]
    else:
        parser.error("--date, --today, or --tomorrow is required")

    total = 0
    for target in targets:
        total += cache_predictions_for_date(target, version=args.version)
    print(f"done: {total} races")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
