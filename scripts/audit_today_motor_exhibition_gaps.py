from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path


DB = Path("C:/boat_project/boatrace-analysis/data/boatrace.db")
STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def yn(v: object) -> bool:
    return v is not None and str(v) != ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        races = conn.execute(
            """
            SELECT race_id, stadium_number, race_number
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (args.date,),
        ).fetchall()
        rows = conn.execute(
            """
            SELECT r.race_id, r.stadium_number, r.race_number,
                   e.boat_number,
                   e.racer_name,
                   e.assigned_motor_number,
                   e.assigned_motor_top_2_percent,
                   e.assigned_motor_top_3_percent,
                   pv.exhibition_time,
                   pv.start_timing_exhibition,
                   pv.tilt_adjustment,
                   oe.lap_time,
                   oe.turn_time,
                   oe.straight_time
              FROM races r
              JOIN race_entries e ON e.race_id = r.race_id
              LEFT JOIN race_previews pv
                ON pv.race_id = r.race_id AND pv.boat_number = e.boat_number
              LEFT JOIN race_original_exhibitions oe
                ON oe.race_id = r.race_id AND oe.boat_number = e.boat_number
             WHERE r.race_date = ?
             ORDER BY r.stadium_number, r.race_number, e.boat_number
            """,
            (args.date,),
        ).fetchall()

    by_race: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_race[str(row["race_id"])].append(row)

    print(f"# Motor/exhibition gap audit {args.date}")
    print(f"races={len(races)} entry_rows={len(rows)} expected_entry_rows={len(races)*6}")

    fields = [
        ("motor_no", "assigned_motor_number"),
        ("motor_2", "assigned_motor_top_2_percent"),
        ("motor_3", "assigned_motor_top_3_percent"),
        ("exhibition_time", "exhibition_time"),
        ("exhibition_st", "start_timing_exhibition"),
        ("tilt", "tilt_adjustment"),
        ("lap", "lap_time"),
        ("turn", "turn_time"),
        ("straight", "straight_time"),
    ]
    totals = {label: 0 for label, _ in fields}
    for row in rows:
        for label, col in fields:
            if not yn(row[col]):
                totals[label] += 1
    print("missing_totals")
    for label, n in totals.items():
        print(f"  {label}: {n}/{len(rows)}")

    print("\nby_stadium")
    by_stadium: dict[int, dict[str, int]] = defaultdict(lambda: {label: 0 for label, _ in fields} | {"rows": 0, "races": 0})
    seen_races = defaultdict(set)
    for row in rows:
        st = int(row["stadium_number"] or 0)
        seen_races[st].add(row["race_id"])
        by_stadium[st]["rows"] += 1
        for label, col in fields:
            if not yn(row[col]):
                by_stadium[st][label] += 1
    for st in sorted(by_stadium):
        d = by_stadium[st]
        d["races"] = len(seen_races[st])
        misses = ", ".join(f"{k}={v}" for k, v in d.items() if k not in {"rows", "races"} and v)
        print(f"  {st:02d} {STADIUM_NAMES.get(st, '?')}: races={d['races']} rows={d['rows']} {misses or 'missing=0'}")

    print("\nproblem_races")
    for race in races:
        rid = str(race["race_id"])
        rr = by_race.get(rid, [])
        if len(rr) != 6:
            print(f"  {rid} entries={len(rr)} expected=6")
            continue
        race_missing = []
        for label, col in fields:
            n = sum(1 for row in rr if not yn(row[col]))
            if n:
                race_missing.append(f"{label}:{n}")
        if race_missing:
            st = int(race["stadium_number"] or 0)
            print(f"  {rid} {STADIUM_NAMES.get(st, '?')}{race['race_number']}R " + " ".join(race_missing))

    print("\nfirst_missing_rows")
    shown = 0
    for row in rows:
        missing = [label for label, col in fields if not yn(row[col])]
        if not missing:
            continue
        st = int(row["stadium_number"] or 0)
        print(
            f"  {row['race_id']} {STADIUM_NAMES.get(st, '?')}{row['race_number']}R "
            f"boat={row['boat_number']} racer={row['racer_name']} missing={','.join(missing)}"
        )
        shown += 1
        if shown >= 80:
            break


if __name__ == "__main__":
    main()
