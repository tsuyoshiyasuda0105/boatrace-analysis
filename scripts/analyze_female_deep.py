"""女性レーサー影響の深掘り分析 4 軸:

  1. 女性 A1 1号艇 → 国1%/グレード/場/年齢で細分
  2. ヴィーナスシリーズ (全 6 艇女性) の ROI と 1着率
  3. 特定女性レーサー (有名どころ) の個別 ROI
  4. 1号艇=男性ベテラン × 同レースに若手女性混入時の挙動

racers.gender / birth_date が populated している前提。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect

conn = connect()


def section(title: str):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ============================================================
# Analysis 1: 女性 A1 1号艇 細分化
# ============================================================
section("【1. 女性 A1 1号艇 細分: 国1%/グレード/場/年齢別 L4 ROI】")

# 1a. 国1% 別
cur = conn.execute("""
WITH base AS (
  SELECT r.race_id, r.race_grade_number, r.stadium_number,
         e.national_top_1_percent AS n1,
         rc.gender,
         (CAST(SUBSTRING(r.race_date FROM 1 FOR 4) AS INT) -
          CAST(SUBSTRING(rc.birth_date FROM 1 FOR 4) AS INT)) AS age,
         pay.payout AS fav_pay,
         p123.payout AS pay_123
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay ON pay.race_id = r.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123
                ON p123.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
     AND rc.gender = 2
)
SELECT
  CASE WHEN n1 >= 7.0 THEN 'A1 女 国1 ge 7'
       WHEN n1 >= 6.0 THEN 'A1 女 国1 6-7'
       ELSE 'A1 女 国1 lt 6'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits,
  SUM(COALESCE(pay_123, 0)) * 1.0 / NULLIF(COUNT(*) * 100, 0) AS roi
  FROM base
 GROUP BY bucket
 ORDER BY MIN(n1) DESC
""")
print(f'{"分類":<18} {"n":>5} {"HIT":>5} {"HIT率":>7} {"ROI":>9}')
print("-" * 50)
for bucket, n, h, roi in cur.fetchall():
    hit_rate = h / n if n else 0
    roi = float(roi) if roi else 0
    print(f"{bucket:<18} {n:>5} {h:>5} {hit_rate*100:>6.2f}% {roi*100:>8.2f}%")

# 1b. グレード別
print()
cur = conn.execute("""
WITH base AS (
  SELECT r.race_grade_number, p123.payout AS pay_123
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay ON pay.race_id = r.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123
                ON p123.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
     AND rc.gender = 2
)
SELECT race_grade_number, COUNT(*) AS n,
       SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       SUM(COALESCE(pay_123, 0)) * 1.0 / NULLIF(COUNT(*) * 100, 0) AS roi
  FROM base GROUP BY race_grade_number ORDER BY race_grade_number
""")
print("グレード別 (女性 A1 1号艇 L4):")
print(f'{"grade":<10} {"n":>5} {"HIT":>5} {"HIT率":>7} {"ROI":>9}')
grade_map = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}
for g, n, h, roi in cur.fetchall():
    hr = h / n if n else 0
    roi = float(roi) if roi else 0
    label = f"{g}:{grade_map.get(g,'?')}" if g else "なし"
    print(f"{label:<10} {n:>5} {h:>5} {hr*100:>6.2f}% {roi*100:>8.2f}%")

# 1c. 年齢別
print()
cur = conn.execute("""
WITH base AS (
  SELECT (CAST(SUBSTRING(r.race_date FROM 1 FOR 4) AS INT) -
          CAST(SUBSTRING(rc.birth_date FROM 1 FOR 4) AS INT)) AS age,
         p123.payout AS pay_123
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay ON pay.race_id = r.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123
                ON p123.race_id = r.race_id
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
     AND rc.gender = 2
     AND rc.birth_date IS NOT NULL
)
SELECT
  CASE WHEN age < 30 THEN '〜29 歳'
       WHEN age < 40 THEN '30-39 歳'
       WHEN age < 50 THEN '40-49 歳'
       ELSE '50+ 歳'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits,
  SUM(COALESCE(pay_123, 0)) * 1.0 / NULLIF(COUNT(*) * 100, 0) AS roi
  FROM base GROUP BY bucket ORDER BY MIN(age)
""")
print("年齢別 (女性 A1 1号艇 L4):")
print(f'{"年齢":<10} {"n":>5} {"HIT":>5} {"HIT率":>7} {"ROI":>9}')
for bucket, n, h, roi in cur.fetchall():
    hr = h / n if n else 0
    roi = float(roi) if roi else 0
    print(f"{bucket:<10} {n:>5} {h:>5} {hr*100:>6.2f}% {roi*100:>8.2f}%")


# ============================================================
# Analysis 2: ヴィーナスシリーズ (全 6 艇女性レース)
# ============================================================
section("【2. ヴィーナスシリーズ等の女性のみレース ROI】")

cur = conn.execute("""
WITH female_count AS (
  SELECT r.race_id, r.race_date, r.race_grade_number, r.race_title,
         COUNT(*) FILTER (WHERE rc.gender = 2) AS n_female,
         COUNT(*) AS n_total
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id
    LEFT JOIN racers rc ON e.racer_number = rc.racer_number
   WHERE r.race_date >= '2022-01-01'
   GROUP BY r.race_id, r.race_date, r.race_grade_number, r.race_title
), with_fav AS (
  SELECT fc.*, pay.payout AS fav_pay, p123.payout AS pay_123,
         e1.class_number AS boat1_cls
    FROM female_count fc
    LEFT JOIN race_entries e1 ON fc.race_id = e1.race_id AND e1.boat_number = 1
    LEFT JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
               WHERE bet_type='trifecta' GROUP BY race_id) pay
           ON pay.race_id = fc.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
               WHERE bet_type='trifecta' AND combination='1-2-3') p123
           ON p123.race_id = fc.race_id
)
SELECT
  CASE WHEN n_female = n_total THEN '全員女性 (ヴィーナス)'
       WHEN n_female >= 4      THEN '女性 4-5 名'
       WHEN n_female = 0       THEN '全員男性'
       ELSE '混在'
  END AS bucket,
  COUNT(*) AS n,
  AVG(fav_pay) AS avg_fav_pay,
  SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits,
  AVG(CASE WHEN pay_123 IS NOT NULL THEN 1.0 ELSE 0 END) AS hit_rate_123,
  AVG(boat1_cls) AS avg_boat1_cls
  FROM with_fav
 WHERE fav_pay IS NOT NULL
 GROUP BY bucket
 ORDER BY MIN(n_female) DESC
""")
print(f'{"カテゴリ":<22} {"n":>6} {"平均本命":>9} {"123 HIT率":>9} {"avg cls":>8}')
print("-" * 65)
for bucket, n, fp, h, hr, avg_cls in cur.fetchall():
    fp = float(fp) if fp else 0
    hr = float(hr) if hr else 0
    avg_cls = float(avg_cls) if avg_cls else 0
    print(f"{bucket:<22} {n:>6,} {fp:>9.0f} {hr*100:>8.2f}% {avg_cls:>8.2f}")

# ヴィーナスの L4 該当
print()
print("ヴィーナス (全 6 艇女性) で L4 戦略を適用すると...")
cur = conn.execute("""
WITH female_count AS (
  SELECT r.race_id, r.stadium_number, e.class_number AS boat1_cls,
         COUNT(*) FILTER (WHERE rc.gender = 2) AS n_female,
         COUNT(*) AS n_total
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    LEFT JOIN race_entries e_all ON r.race_id = e_all.race_id
    LEFT JOIN racers rc ON e_all.racer_number = rc.racer_number
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
   GROUP BY r.race_id, r.stadium_number, e.class_number
)
SELECT
  CASE WHEN fc.n_female = 6 THEN 'ヴィーナス (6 女)'
       WHEN fc.n_female = 0 THEN '男性のみ' END AS bucket,
  COUNT(*) AS n_l4,
  SUM(CASE WHEN p123.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
  SUM(COALESCE(p123.payout, 0)) * 1.0 / NULLIF(COUNT(*)*100, 0) AS roi
  FROM female_count fc
  JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
         WHERE bet_type='trifecta' GROUP BY race_id) pay
       ON pay.race_id = fc.race_id
  LEFT JOIN (SELECT race_id, payout FROM race_payouts
              WHERE bet_type='trifecta' AND combination='1-2-3') p123
       ON p123.race_id = fc.race_id
 WHERE fc.boat1_cls = 1
   AND pay.payout BETWEEN 500 AND 999
   AND (fc.n_female = 6 OR fc.n_female = 0)
 GROUP BY bucket
""")
for bucket, n, h, roi in cur.fetchall():
    hr = h / n if n else 0
    roi = float(roi) if roi else 0
    print(f"  {bucket or '?':<22} n={n:>4} HIT={h:>4} ({hr*100:>5.2f}%) ROI={roi*100:>6.2f}%")


# ============================================================
# Analysis 3: 個別女性レーサー ROI
# ============================================================
section("【3. 主要女性レーサー個別: 1号艇 L4 該当時の ROI】")

cur = conn.execute("""
WITH base AS (
  SELECT e.racer_number, e.racer_name,
         pay.payout AS fav_pay, p123.payout AS pay_123,
         res.finishing_position AS pos1
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number AND rc.gender = 2
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay
         ON pay.race_id = r.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123
         ON p123.race_id = r.race_id
    LEFT JOIN race_results res ON res.race_id = r.race_id AND res.boat_number = 1
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND e.class_number = 1
     AND pay.payout BETWEEN 500 AND 999
)
SELECT racer_number, MIN(racer_name) AS name,
       COUNT(*) AS n,
       SUM(CASE WHEN pos1 = 1 THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits_123,
       SUM(COALESCE(pay_123, 0)) * 1.0 / NULLIF(COUNT(*)*100, 0) AS roi_123
  FROM base
 GROUP BY racer_number
HAVING COUNT(*) >= 3
 ORDER BY n DESC
 LIMIT 25
""")
rows = cur.fetchall()
print(f"L4 該当 3 回以上 の女性 1号艇レーサー (上位 25 名):")
print(f'{"toban":>6} {"名前":<22} {"n":>4} {"1着":>4} {"1着率":>7} {"123 HIT":>8} {"ROI":>8}')
print("-" * 70)
total_n = 0
total_hits = 0
total_pay = 0
for tb, name, n, w, h, roi in rows:
    win_rate = w / n if n else 0
    roi = float(roi) if roi else 0
    name = (name or "")[:18]
    print(f"{tb:>6} {name:<22} {n:>4} {w:>4} {win_rate*100:>6.2f}% {h:>8} {roi*100:>7.2f}%")
    total_n += n
    total_hits += h
print(f"\n上記 25 名の合計: n={total_n}, hits_123={total_hits}")


# ============================================================
# Analysis 4: 男性ベテラン 1号艇 × 同レースに若手女性混入
# ============================================================
section("【4. 1号艇=男性ベテラン (35+ 歳) × 同レース内に若手女性 (28- 歳)】")

cur = conn.execute("""
WITH boat1 AS (
  SELECT r.race_id, r.race_date,
         (CAST(SUBSTRING(r.race_date FROM 1 FOR 4) AS INT) -
          CAST(SUBSTRING(rc.birth_date FROM 1 FOR 4) AS INT)) AS boat1_age,
         rc.gender AS boat1_gender,
         e.class_number AS boat1_cls
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
    JOIN racers rc ON e.racer_number = rc.racer_number
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
     AND rc.birth_date IS NOT NULL
), young_female AS (
  SELECT r.race_id,
         COUNT(*) FILTER (
           WHERE rc.gender = 2
             AND (CAST(SUBSTRING(r.race_date FROM 1 FOR 4) AS INT) -
                  CAST(SUBSTRING(rc.birth_date FROM 1 FOR 4) AS INT)) <= 28
         ) AS n_young_female
    FROM races r
    JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number != 1
    JOIN racers rc ON e.racer_number = rc.racer_number
   WHERE r.race_date >= '2022-01-01'
     AND rc.birth_date IS NOT NULL
   GROUP BY r.race_id
), final AS (
  SELECT b.race_id, b.boat1_age, b.boat1_gender, b.boat1_cls,
         COALESCE(yf.n_young_female, 0) AS n_young_female,
         pay.payout AS fav_pay,
         p123.payout AS pay_123,
         res.finishing_position AS pos1
    FROM boat1 b
    LEFT JOIN young_female yf ON yf.race_id = b.race_id
    JOIN (SELECT race_id, MIN(payout) AS payout FROM race_payouts
           WHERE bet_type='trifecta' GROUP BY race_id) pay ON pay.race_id = b.race_id
    LEFT JOIN (SELECT race_id, payout FROM race_payouts
                WHERE bet_type='trifecta' AND combination='1-2-3') p123
                ON p123.race_id = b.race_id
    LEFT JOIN race_results res ON res.race_id = b.race_id AND res.boat_number = 1
   WHERE b.boat1_cls = 1 AND pay.payout BETWEEN 500 AND 999
)
SELECT
  CASE WHEN boat1_gender = 1 AND boat1_age >= 35 AND n_young_female >= 1
            THEN '男ベテラン 1号艇 × 若手女性混入'
       WHEN boat1_gender = 1 AND boat1_age >= 35
            THEN '男ベテラン 1号艇 (若手女性なし)'
       WHEN boat1_gender = 1 AND boat1_age < 35
            THEN '男若手 1号艇'
       WHEN boat1_gender = 2
            THEN '女性 1号艇'
  END AS bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN pos1 = 1 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN pay_123 IS NOT NULL THEN 1 ELSE 0 END) AS hits_123,
  SUM(COALESCE(pay_123, 0)) * 1.0 / NULLIF(COUNT(*)*100, 0) AS roi_123
  FROM final
 GROUP BY bucket
 ORDER BY n DESC
""")
print(f'{"カテゴリ":<32} {"n":>5} {"1着率":>7} {"123 HIT率":>9} {"ROI(1-2-3)":>11}')
print("-" * 75)
for bucket, n, w, h, roi in cur.fetchall():
    win_rate = w / n if n else 0
    hit_rate = h / n if n else 0
    roi = float(roi) if roi else 0
    print(f"{bucket or '?':<32} {n:>5} {win_rate*100:>6.2f}% {hit_rate*100:>8.2f}% {roi*100:>10.2f}%")
