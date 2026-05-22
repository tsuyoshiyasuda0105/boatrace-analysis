"""Venus races (n_female=6) detailed analysis for dedicated L4 path"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect

conn = connect()

print("=== Venus L4候補レース 詳細分析 ===\n")

GRADE_NAME = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}

# 1. グレード別 ROI (B除外あり)
sql = """
SELECT r.race_grade_number, COUNT(*) AS n,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN 1 ELSE 0 END) AS hits,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN COALESCE(pt.payout,0) ELSE 0 END) AS pay
FROM races r
LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
LEFT JOIN (
    SELECT ef.race_id, COUNT(*) AS n_female
    FROM race_entries ef
    JOIN racers rc ON rc.racer_number = ef.racer_number
    WHERE rc.gender = 2
    GROUP BY ef.race_id
) fem ON fem.race_id = r.race_id
LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
WHERE e.class_number = 1
  AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
  AND (pv.weather_number IS NULL OR pv.weather_number != 3)
  AND COALESCE(fem.n_female, 0) = 6
  AND EXISTS (SELECT 1 FROM race_payouts p2
              WHERE p2.race_id=r.race_id AND p2.bet_type='trifecta'
                AND p2.payout >= 500 AND p2.payout < 1000)
  AND res1.boat_number IS NOT NULL
GROUP BY r.race_grade_number ORDER BY 1
"""
print("グレード別 (B除外+A1+雨除外):")
print(f"  {'グレード':>6} | {'n':>5} | {'的中率':>7} | {'ROI':>7}")
for row in conn.execute(sql).fetchall():
    g, n, hits, pay = row
    gname = GRADE_NAME.get(g, f"G{g}") if g else "不明"
    print(f"  {gname:>6} | {n:>5} | {hits/n*100:>6.1f}% | {pay/(n*100)*100:>6.1f}%")

# 2. B除外なし全体
sql2 = """
SELECT COUNT(*) AS n,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN 1 ELSE 0 END) AS hits,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN COALESCE(pt.payout,0) ELSE 0 END) AS pay
FROM races r
LEFT JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
LEFT JOIN (SELECT ef.race_id, COUNT(*) AS n_female FROM race_entries ef
           JOIN racers rc ON rc.racer_number=ef.racer_number
           WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
WHERE e.class_number=1 AND COALESCE(fem.n_female,0)=6
  AND EXISTS (SELECT 1 FROM race_payouts p2
              WHERE p2.race_id=r.race_id AND p2.bet_type='trifecta'
                AND p2.payout>=500 AND p2.payout<1000)
  AND res1.boat_number IS NOT NULL
"""
n, hits, pay = conn.execute(sql2).fetchone()
print(f"\nB除外なし (A1+雨除外なし): n={n}, ROI={pay/(n*100)*100:.1f}%")

# 3. クラス条件なし (A1以外も含む)
sql3 = """
SELECT e.class_number, COUNT(*) AS n,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN 1 ELSE 0 END) AS hits,
       SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN COALESCE(pt.payout,0) ELSE 0 END) AS pay
FROM races r
LEFT JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
LEFT JOIN (SELECT ef.race_id, COUNT(*) AS n_female FROM race_entries ef
           JOIN racers rc ON rc.racer_number=ef.racer_number
           WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
WHERE COALESCE(fem.n_female,0)=6
  AND EXISTS (SELECT 1 FROM race_payouts p2
              WHERE p2.race_id=r.race_id AND p2.bet_type='trifecta'
                AND p2.payout>=500 AND p2.payout<1000)
  AND res1.boat_number IS NOT NULL
GROUP BY e.class_number ORDER BY 1
"""
cls_name = {1:"A1", 2:"A2", 3:"B1", 4:"B2", None:"不明"}
print("\nクラス別 (B除外・雨なし):")
print(f"  {'クラス':>5} | {'n':>5} | {'的中率':>7} | {'ROI':>7}")
for row in conn.execute(sql3).fetchall():
    cls, n, hits, pay = row
    print(f"  {cls_name.get(cls,'?'):>5} | {n:>5} | {hits/n*100:>6.1f}% | {pay/(n*100)*100:>6.1f}%")

# 4. A1のみ、B除外なし、全グレード合計
print("\n=== 採用基準候補比較 ===")
cases = [
    ("案1: A1+B除外+雨除外", 1, True, True),
    ("案2: A1+B除外のみ",    1, True, False),
    ("案3: A1のみ",          1, False, False),
    ("案4: 全クラス",         None, False, False),
]
for label, cls_req, b_excl, rain_excl in cases:
    wheres = ["COALESCE(fem.n_female,0)=6",
              "EXISTS (SELECT 1 FROM race_payouts p2 WHERE p2.race_id=r.race_id AND p2.bet_type='trifecta' AND p2.payout>=500 AND p2.payout<1000)",
              "res1.boat_number IS NOT NULL"]
    joins = ""
    if rain_excl:
        wheres.append("(pv.weather_number IS NULL OR pv.weather_number != 3)")
        joins = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1"
    if b_excl:
        wheres.append("r.stadium_number NOT IN (2,4,7,8,10,19,21,24)")
    if cls_req:
        wheres.append(f"e.class_number = {cls_req}")
    w = " AND ".join(wheres)
    q = f"""
    SELECT COUNT(*) AS n,
           SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN 1 ELSE 0 END),
           SUM(CASE WHEN res1.boat_number=1 AND res2.boat_number=2 AND res3.boat_number=3 THEN COALESCE(pt.payout,0) ELSE 0 END)
    FROM races r
    LEFT JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS n_female FROM race_entries ef
               JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    {joins}
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
    LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
    LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
    LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
    WHERE {w}
    """
    row = conn.execute(q).fetchone()
    n, hits, pay = row
    if n:
        print(f"  {label}: n={n:>5}, ROI={pay/(n*100)*100:.1f}%")

conn.close()
