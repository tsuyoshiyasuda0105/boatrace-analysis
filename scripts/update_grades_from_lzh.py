"""B file を再 parse して race_grade_number を一括 UPDATE。

LZH B file には大会名が含まれており、parser に grade 推測ロジック
(GRADE_KEYWORDS) を追加したので、それで grade を補完する。

使い方:
    python scripts/update_grades_from_lzh.py
    python scripts/update_grades_from_lzh.py --target supabase
"""
from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from src.db.connection import connect as db_connect
from src.parsers.official_b import parse_b_text


JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["local", "supabase"], default="local",
                   help="UPDATE 先 (default=local)")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=_today_jst().isoformat())
    args = p.parse_args()

    if args.target == "local":
        os.environ.pop("DATABASE_URL", None)
        os.environ["DATABASE_URL"] = ""

    pattern = str(config.OFFICIAL_PROGRAMS_DIR / "B*.TXT")
    b_files = sorted(glob.glob(pattern))
    print(f"B files found: {len(b_files)}")

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)

    total_updates = 0
    grade_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, None: 0}

    with db_connect() as conn:
        for fp in b_files:
            # B240101.TXT → 2024-01-01
            fname = Path(fp).name  # B240101.TXT
            try:
                yy = int(fname[1:3])
                mm = int(fname[3:5])
                dd = int(fname[5:7])
                fdate = date(2000 + yy, mm, dd)
            except (ValueError, IndexError):
                continue
            if fdate < start_d or fdate > end_d:
                continue

            try:
                text = Path(fp).read_bytes().decode("cp932", errors="replace")
                parsed = parse_b_text(text, fdate)
            except Exception as e:
                print(f"  parse fail {fname}: {e}")
                continue

            batch = []
            for r in parsed:
                g = r.get("race_grade_number")
                grade_counts[g] = grade_counts.get(g, 0) + 1
                if g is not None:
                    batch.append((g, r["race_id"]))
            if batch:
                conn.executemany(
                    "UPDATE races SET race_grade_number = ? WHERE race_id = ? AND race_grade_number IS NULL",
                    batch,
                )
                total_updates += len(batch)
        conn.commit()

    print(f"\n=== 完了 ===")
    print(f"UPDATE 試行: {total_updates}")
    print(f"grade 分布 (推測):")
    for g, n in sorted(grade_counts.items(), key=lambda x: (x[0] is None, x[0])):
        name = {1:"SG", 2:"G1", 3:"G2", 4:"G3", 5:"一般戦", None:"None"}.get(g, str(g))
        print(f"  {g} ({name}): {n}")


if __name__ == "__main__":
    main()
