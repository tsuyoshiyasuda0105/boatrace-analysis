from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path("C:/boat_project/boatrace-analysis")
sys.path.insert(0, str(REPO))

from src.db.connection import connect as pg_connect

JST = ZoneInfo("Asia/Tokyo")


def parse_close(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return dt.replace(tzinfo=JST) if dt.tzinfo is None else dt.astimezone(JST)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    now = datetime.now(JST)
    with pg_connect() as conn:
        cur = conn.execute(
            """
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   COUNT(p.race_id) AS preview_rows,
                   SUM(CASE WHEN p.exhibition_time IS NOT NULL AND p.exhibition_time != 0 THEN 1 ELSE 0 END) AS ex_time_rows,
                   SUM(CASE WHEN p.start_timing_exhibition IS NOT NULL THEN 1 ELSE 0 END) AS ex_st_rows,
                   SUM(CASE WHEN o.race_id IS NOT NULL THEN 1 ELSE 0 END) AS original_rows
              FROM races r
              LEFT JOIN race_previews p ON p.race_id = r.race_id
              LEFT JOIN race_original_exhibitions o ON o.race_id = r.race_id
             WHERE r.race_date = %s
             GROUP BY r.race_id, r.stadium_number, r.race_number, r.race_closed_at
             ORDER BY r.race_closed_at
            """,
            (args.date,),
        )
        rows = cur.fetchall()
    lines = [
        f"# Today exhibition due-window report {args.date}",
        "",
        f"- now_jst: {now.isoformat(timespec='seconds')}",
        "- beforeinfo target window: close minus 5 to 9 minutes",
        "",
        "| race | close | minutes_until | preview | ex_time | ex_st | original | status |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for rid, st, rno, closed_at, preview_rows, ex_time_rows, ex_st_rows, original_rows in rows[:40]:
        close = parse_close(closed_at)
        if close is None:
            mins = None
            status = "no close"
        else:
            mins_f = (close - now).total_seconds() / 60
            mins = f"{mins_f:.1f}"
            if 5 <= mins_f <= 9:
                status = "due now"
            elif mins_f > 9:
                status = "not due yet"
            elif mins_f >= -900:
                status = "recovery window"
            else:
                status = "past recovery"
        lines.append(
            f"| {rid} {int(st):02d}-{int(rno):02d} | {str(closed_at)[11:16]} | "
            f"{mins if mins is not None else '-'} | {int(preview_rows or 0)}/6 | "
            f"{int(ex_time_rows or 0)}/6 | {int(ex_st_rows or 0)}/6 | "
            f"{int(original_rows or 0)}/6 | {status} |"
        )
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
