"""T-5/T-4/T-3/T-2/T-1 スナップショットの実測ラグを計測。
recorded_at (UTC string) と target_time (race_closed_at - N分) との差を集計。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from datetime import datetime, timedelta, timezone
from src.db.connection import connect

JST = timezone(timedelta(hours=9))
UTC = timezone.utc

conn = connect()
cur = conn.execute("""SELECT o.race_id, o.snapshot_label, o.recorded_at, r.race_closed_at, r.race_date
  FROM odds_trifecta o JOIN races r ON o.race_id = r.race_id
  WHERE o.combination = '1-2-3'
    AND o.snapshot_label IN ('T-5min','T-4min','T-3min','T-2min','T-1min')
    AND o.recorded_at >= '2026-05-15'
  ORDER BY o.recorded_at DESC LIMIT 1000""")
rows = cur.fetchall()
print(f'rows={len(rows)}')

by_label = {}
for rid, label, rec, closed, rd in rows:
    try:
        rec_utc = datetime.fromisoformat(rec).replace(tzinfo=UTC)
        rec_jst = rec_utc.astimezone(JST)
        if ' ' in closed:
            closed_dt = datetime.fromisoformat(closed).replace(tzinfo=JST)
        else:
            closed_dt = datetime.fromisoformat(f'{rd} {closed}').replace(tzinfo=JST)
        mins = int(label.replace('T-', '').replace('min', ''))
        target = closed_dt - timedelta(minutes=mins)
        lag = (rec_jst - target).total_seconds()
        by_label.setdefault(label, []).append(lag)
    except Exception:
        pass

import statistics
print()
print('=== DB 書込ラグ (recorded_at - 理想 target time) ===')
print(f'{"label":<8} {"n":>4} {"min":>7} {"p25":>7} {"med":>7} {"mean":>7} {"p75":>7} {"p90":>7} {"max":>7}')
for lbl in ['T-5min', 'T-4min', 'T-3min', 'T-2min', 'T-1min']:
    if lbl in by_label:
        v = sorted(by_label[lbl])
        n = len(v)
        p25 = v[max(0, n // 4)]
        p75 = v[min(n - 1, (3 * n) // 4)]
        p90 = v[min(n - 1, (9 * n) // 10)]
        print(f'{lbl:<8} {n:>4} {min(v):>+7.1f} {p25:>+7.1f} {statistics.median(v):>+7.1f} {statistics.mean(v):>+7.1f} {p75:>+7.1f} {p90:>+7.1f} {max(v):>+7.1f}')
