"""案A 女性フィルタの実装検証。

main L4 SQL に「女性0名」フィルタを足した後の ROI が
analyze_female_impact.py の '0人(男性のみ)' = 180.84% に一致するか確認。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect

conn = connect()
B = "(2,4,7,8,10,19,21,24)"

# 女性フィルタ あり / なし で ROI を比較 (確定払戻ベース、3連単1-2-3)
sql = f"""
WITH base AS (
  SELECT r.race_id,
         COALESCE(fem.n_female, 0) AS n_female,
         pay.payout AS fav_pay,
         p123.payout AS pay_123
    FROM races r
    JOIN race_entries e ON e.race_id = r.race_id AND e.boat_number = 1
    LEFT JOIN (
       SELECT ef.race_id, COUNT(*) AS n_female
         FROM race_entries ef JOIN racers rc ON rc.racer_number = ef.racer_number
        WHERE rc.gender = 2 GROUP BY ef.race_id
    ) fem ON fem.race_id = r.race_id
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay ON pay.race_id = r.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123 ON p123.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN {B}
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
)
SELECT label, COUNT(*) AS n,
       SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       SUM(COALESCE(pay_123,0))*1.0/(COUNT(*)*100) AS roi
  FROM (
    SELECT 'フィルタなし(全部)' AS label, n_female, pay_123 FROM base
    UNION ALL
    SELECT '案A(女性0名のみ)' AS label, n_female, pay_123 FROM base WHERE n_female = 0
  ) x
 GROUP BY label
 ORDER BY label
"""
cur = conn.execute(sql)
print(f'{"戦略":<20} {"L4該当":>7} {"1-2-3 HIT":>10} {"HIT率":>8} {"ROI":>9}')
print("-" * 60)
for label, n, h, roi in cur.fetchall():
    hr = h / n if n else 0
    print(f"{label:<20} {n:>7,} {h:>10,} {hr*100:>7.2f}% {float(roi or 0)*100:>8.2f}%")
