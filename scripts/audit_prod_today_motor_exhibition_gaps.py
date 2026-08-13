from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path


REPO = Path("C:/boat_project/boatrace-analysis")
sys.path.insert(0, str(REPO))

from src.db.connection import connect as pg_connect


STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def present(v: object) -> bool:
    return v is not None and str(v) != ""


def fetch_rows(target_date: str) -> tuple[list[dict], list[dict]]:
    with pg_connect() as conn:
        cur = conn.execute(
            """
            SELECT race_id, stadium_number, race_number
              FROM races
             WHERE race_date = %s
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        )
        races = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        cur = conn.execute(
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
             WHERE r.race_date = %s
             ORDER BY r.stadium_number, r.race_number, e.boat_number
            """,
            (target_date,),
        )
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    return races, rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    races, rows = fetch_rows(args.date)
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
    lines = []
    lines.append(f"# Production motor/exhibition gap audit {args.date}")
    lines.append("")
    lines.append(f"- races: {len(races)}")
    lines.append(f"- entry rows: {len(rows)} / expected {len(races) * 6}")
    lines.append("")
    lines.append("## Missing totals")
    lines.append("")
    lines.append("| field | missing |")
    lines.append("|---|---:|")
    for label, col in fields:
        lines.append(f"| {label} | {sum(1 for r in rows if not present(r.get(col)))}/{len(rows)} |")

    by_stadium = defaultdict(lambda: {"rows": 0, "races": set(), **{label: 0 for label, _ in fields}})
    for row in rows:
        st = int(row.get("stadium_number") or 0)
        by_stadium[st]["rows"] += 1
        by_stadium[st]["races"].add(row.get("race_id"))
        for label, col in fields:
            if not present(row.get(col)):
                by_stadium[st][label] += 1
    lines.append("")
    lines.append("## By stadium")
    lines.append("")
    lines.append("| stadium | races | rows | missing fields |")
    lines.append("|---|---:|---:|---|")
    for st in sorted(by_stadium):
        d = by_stadium[st]
        misses = ", ".join(f"{k}={v}" for k, v in d.items() if k not in {"rows", "races"} and v)
        lines.append(f"| {st:02d} {STADIUM_NAMES.get(st, '?')} | {len(d['races'])} | {d['rows']} | {misses or '-'} |")

    by_race = defaultdict(list)
    for row in rows:
        by_race[row["race_id"]].append(row)
    lines.append("")
    lines.append("## Problem races")
    lines.append("")
    lines.append("| race | missing |")
    lines.append("|---|---|")
    for race in races:
        rr = by_race.get(race["race_id"], [])
        parts = []
        if len(rr) != 6:
            parts.append(f"entries={len(rr)}")
        for label, col in fields:
            n = sum(1 for row in rr if not present(row.get(col)))
            if n:
                parts.append(f"{label}:{n}")
        if parts:
            st = int(race.get("stadium_number") or 0)
            lines.append(f"| {race['race_id']} {STADIUM_NAMES.get(st, '?')}{race['race_number']}R | {' '.join(parts)} |")
    text = "\n".join(lines)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text[:6000])


if __name__ == "__main__":
    main()
