"""女性レーサーが入っているレースの ROI / 勝率影響を分析。

分析軸:
  1. 女性 0 / 1 / 2+ 人入りの全体勝率 (1号艇1着率) + 三連単本命 ROI
  2. 女性がいる艇の番号別の影響 (1号艇に女性 vs 4-6号艇に女性)
  3. L4 戦略 (本命 500-1000円帯) 候補内で、女性入り vs 男性のみの ROI
  4. 1号艇 A1 男性 + 他艇に女性混入の挙動 (1号艇1着が出やすい等の仮説検証)

データ:
  - racers.gender (1:男 2:女)
  - race_entries + race_results
  - race_payouts (確定払戻)

期間: 2022-01-01 〜 直近 (バックテスト全期間)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect

conn = connect()

# ============================================================
# 1. 全体: 女性 0 人 / 1 人 / 2+ 人 のレースで勝率と ROI
# ============================================================
print("=" * 72)
print("【1. 女性レーサー人数別: 1号艇 1着率 と 三連単本命 ROI】")
print("=" * 72)
cur = conn.execute("""
WITH race_female_count AS (
  SELECT r.race_id, r.race_date, r.race_grade_number,
         COUNT(*) FILTER (WHERE rc.gender = 2) AS n_female,
         COUNT(*)                              AS n_total
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id
    LEFT JOIN racers rc ON e.racer_number = rc.racer_number
   WHERE r.race_date >= '2022-01-01'
   GROUP BY r.race_id, r.race_date, r.race_grade_number
), with_result AS (
  SELECT rfc.race_id, rfc.n_female, rfc.race_grade_number,
         CASE WHEN res.boat_number = 1 AND res.finishing_position = 1 THEN 1 ELSE 0 END AS boat1_won,
         pay.payout AS tri_pay_fav
    FROM race_female_count rfc
    LEFT JOIN race_results res ON rfc.race_id = res.race_id AND res.boat_number = 1
    LEFT JOIN (
      SELECT race_id, MIN(payout) AS payout
        FROM race_payouts WHERE bet_type = 'trifecta'
        GROUP BY race_id
    ) pay ON rfc.race_id = pay.race_id
)
SELECT
    CASE WHEN n_female = 0 THEN '0 人'
         WHEN n_female = 1 THEN '1 人'
         WHEN n_female = 2 THEN '2 人'
         WHEN n_female >= 3 THEN '3+ 人'
    END AS bucket,
    COUNT(*) AS n_races,
    SUM(boat1_won) AS boat1_wins,
    AVG(boat1_won) AS boat1_win_rate,
    AVG(tri_pay_fav) AS avg_tri_pay
  FROM with_result
 WHERE tri_pay_fav IS NOT NULL
 GROUP BY bucket
 ORDER BY MIN(n_female)
""")
print(f'{"女性人数":<10} {"レース数":>8} {"1号艇 1着率":>10} {"平均本命払戻":>13}')
print("-" * 50)
total_races = 0
for bucket, n, wins, rate, pay in cur.fetchall():
    total_races += n
    pay = float(pay) if pay else 0
    print(f"{bucket:<10} {n:>8,} {rate*100:>9.2f}% {pay:>13,.0f}")
print(f"{'合計':<10} {total_races:>8,}")

# ============================================================
# 2. 女性が乗っている艇の番号別 1着率
# ============================================================
print()
print("=" * 72)
print("【2. 女性が乗っている艇番別: その艇の 1着率 (vs 全体平均)】")
print("=" * 72)
cur = conn.execute("""
SELECT e.boat_number,
       COUNT(*) FILTER (WHERE rc.gender = 2) AS n_female_seats,
       COUNT(*) AS n_total_seats,
       AVG(CASE WHEN rc.gender = 2 AND res.finishing_position = 1 THEN 1.0
                WHEN rc.gender = 2 THEN 0.0 END) AS female_win_rate,
       AVG(CASE WHEN rc.gender = 1 AND res.finishing_position = 1 THEN 1.0
                WHEN rc.gender = 1 THEN 0.0 END) AS male_win_rate
  FROM race_entries e
  JOIN races r ON r.race_id = e.race_id
  JOIN racers rc ON e.racer_number = rc.racer_number
  JOIN race_results res ON res.race_id = e.race_id AND res.boat_number = e.boat_number
 WHERE r.race_date >= '2022-01-01'
 GROUP BY e.boat_number
 ORDER BY e.boat_number
""")
print(f'{"艇番":<5} {"女性出走":>9} {"男性出走":>9} {"女性 1着率":>10} {"男性 1着率":>10} {"差(F-M)":>9}')
print("-" * 65)
for b, nf, nt, fr, mr in cur.fetchall():
    nm = nt - nf
    fr = float(fr) if fr else 0
    mr = float(mr) if mr else 0
    diff = (fr - mr) * 100
    print(f"{b:<5} {nf:>9,} {nm:>9,} {fr*100:>9.2f}% {mr*100:>9.2f}% {diff:>+8.2f}%")

# ============================================================
# 3. L4 戦略 (本命 500-1000 円) で女性入り vs 男性のみ
# ============================================================
print()
print("=" * 72)
print("【3. L4 戦略 (1号艇A1+B除外+本命500-1000): 女性人数別 ROI】")
print("=" * 72)
cur = conn.execute("""
WITH l4_races AS (
  SELECT r.race_id, r.race_date, r.race_grade_number,
         COUNT(*) FILTER (WHERE rc.gender = 2) AS n_female,
         MAX(CASE WHEN e.boat_number=1 THEN e.class_number END) AS boat1_cls,
         pay.payout AS fav_pay
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id
    LEFT JOIN racers rc ON e.racer_number = rc.racer_number
    JOIN (
      SELECT race_id, MIN(payout) AS payout
        FROM race_payouts WHERE bet_type='trifecta'
        GROUP BY race_id
    ) pay ON pay.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
   GROUP BY r.race_id, r.race_date, r.race_grade_number, pay.payout
), filtered AS (
  SELECT lr.*, res.finishing_position
    FROM l4_races lr
    LEFT JOIN race_results res ON lr.race_id = res.race_id AND res.boat_number = 1
   WHERE lr.boat1_cls = 1
     AND lr.fav_pay BETWEEN 500 AND 999
), hits AS (
  SELECT f.*, pay_123.combo_payout
    FROM filtered f
    LEFT JOIN (
      SELECT race_id, payout AS combo_payout
        FROM race_payouts
       WHERE bet_type='trifecta' AND combination='1-2-3'
    ) pay_123 ON f.race_id = pay_123.race_id
)
SELECT
    CASE WHEN n_female = 0 THEN '0 人 (男性のみ)'
         WHEN n_female = 1 THEN '1 人'
         WHEN n_female >= 2 THEN '2+ 人'
    END AS bucket,
    COUNT(*) AS n_l4,
    SUM(CASE WHEN combo_payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hits,
    SUM(COALESCE(combo_payout, 0)) AS total_payout,
    SUM(CASE WHEN combo_payout IS NOT NULL THEN 1.0 ELSE 0 END) / COUNT(*) AS hit_rate,
    SUM(COALESCE(combo_payout, 0)) * 1.0 / (COUNT(*) * 100) AS roi_3tan_123
  FROM hits
 GROUP BY bucket
 ORDER BY MIN(n_female)
""")
print(f'{"女性人数":<14} {"L4該当":>8} {"1-2-3 ヒット":>11} {"HIT率":>8} {"ROI(3連単1-2-3)":>16}')
print("-" * 65)
for bucket, n, h, total, hit_rate, roi in cur.fetchall():
    hit_rate = float(hit_rate) if hit_rate else 0
    roi = float(roi) if roi else 0
    print(f"{bucket:<14} {n:>8,} {h:>11,} {hit_rate*100:>7.2f}% {roi*100:>15.2f}%")

# ============================================================
# 4. 1号艇に女性 vs 1号艇が男性 (L4 内)
# ============================================================
print()
print("=" * 72)
print("【4. L4 戦略内: 1号艇の性別による違い】")
print("=" * 72)
cur = conn.execute("""
WITH base AS (
  SELECT r.race_id, e.boat_number, rc.gender,
         pay.payout AS fav_pay
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number
    JOIN (
      SELECT race_id, MIN(payout) AS payout
        FROM race_payouts WHERE bet_type='trifecta'
        GROUP BY race_id
    ) pay ON pay.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
)
SELECT b.gender,
       COUNT(*) AS n,
       SUM(CASE WHEN p123.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hits,
       SUM(COALESCE(p123.payout, 0)) * 1.0 / (COUNT(*) * 100) AS roi_123
  FROM base b
  LEFT JOIN (
    SELECT race_id, payout FROM race_payouts
     WHERE bet_type='trifecta' AND combination='1-2-3'
  ) p123 ON b.race_id = p123.race_id
 GROUP BY b.gender
 ORDER BY b.gender
""")
print(f'{"1号艇 性別":<12} {"L4該当":>8} {"1-2-3 HIT":>11} {"HIT率":>8} {"ROI":>10}')
print("-" * 52)
gmap = {1: "男性", 2: "女性"}
for g, n, h, roi in cur.fetchall():
    hit_rate = h / n if n else 0
    roi = float(roi) if roi else 0
    print(f"{gmap.get(g, '?'):<12} {n:>8,} {h:>11,} {hit_rate*100:>7.2f}% {roi*100:>9.2f}%")
