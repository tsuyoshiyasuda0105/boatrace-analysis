"""Read-only smoke check against one completed local race."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.db.connection import connect
from src.start_prediction.features import PointInTimeFeatureBuilder
from src.start_prediction.models import RuleEnsembleV1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-07-20")
    args = parser.parse_args()
    with connect(config.DB_PATH) as conn:
        row = conn.execute(
            """SELECT r.race_id FROM races r
                JOIN race_previews p ON p.race_id=r.race_id
                JOIN race_results rr ON rr.race_id=r.race_id
               WHERE r.race_date=? GROUP BY r.race_id
              HAVING COUNT(DISTINCT p.boat_number)=6 AND COUNT(DISTINCT rr.boat_number)=6
               LIMIT 1""", (args.date,),
        ).fetchone()
        if not row:
            raise LookupError(f"no complete race on {args.date}")
        snapshot = PointInTimeFeatureBuilder(conn).build(str(row[0]), "post_exhibition")
        output = RuleEnsembleV1().predict(snapshot.as_dict())
    sums = {name: round(sum(float(x[name]) for x in output["boats"]), 8)
            for name in ("first_probability", "second_probability", "third_probability")}
    assert all(abs(value - 1.0) < 1e-6 for value in sums.values()), sums
    assert len(output["trifectas"]) == 10
    print({"race_id": row[0], "position_probability_sums": sums,
           "confidence": round(output["confidence"], 3),
           "attack": output["primary_attack_boat"], "leader": output["first_mark_boat"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
