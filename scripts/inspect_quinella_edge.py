from __future__ import annotations

import json
import sqlite3
import sys


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/boatrace.db"
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    print("TABLES")
    print(
        json.dumps(
            [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name")],
            ensure_ascii=False,
        )
    )

    for table in [
        "races",
        "race_entries",
        "race_results",
        "race_payouts",
        "race_predictions",
        "odds_trifecta",
        "l4_daily_stats",
    ]:
        print("TABLE", table)
        print(cur.execute(f"pragma table_info({table})").fetchall())

    queries = [
        "select min(race_date), max(race_date), count(*) from races",
        """
        select bet_type, count(*), min(payout), max(payout)
          from race_payouts
         group by bet_type
         order by bet_type
        """,
        """
        select min(r.race_date), max(r.race_date), count(distinct p.race_id)
          from race_payouts p
          join races r on r.race_id = p.race_id
         where p.bet_type = 'quinella'
        """,
    ]
    for query in queries:
        print("QUERY")
        print(cur.execute(query).fetchall())


if __name__ == "__main__":
    main()
