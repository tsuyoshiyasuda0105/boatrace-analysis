"""L4 (案A: 男性のみ) における 1号艇 単勝の損益分岐オッズを算出。

単勝の期待値: EV = P(1号艇1着) × odds - 1
  → EV > 0 の条件: odds > 1 / P(1号艇1着)
  → 損益分岐オッズ = 1 / 1着率

実データで:
  1. L4 全体の 1号艇1着率 P → 損益分岐 = 1/P
  2. 3連単本命オッズ帯別の 1着率 (強い本命ほど P 高い = 閾値低い)
  3. 実現単勝オッズ分布 (勝ったレースの payout/100) と単勝 ROI
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect

conn = connect()
B = "(2,4,7,8,10,19,21,24)"

# L4 universe (案A 男性のみ, B除外, 雨除外, grade1-4)
# ★ 事前オッズ基準: odds_trifecta 1-2-3 が T-X で 5-10倍 (=¥500-999) 帯にあったレース。
#   race_payouts MIN (事後確定払戻) は CLAUDE.md 禁止パターンのため不使用。
base = f"""
WITH t5 AS (
  SELECT race_id,
         MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4,
         MIN(CASE WHEN snapshot_label='T-5min' THEN odds END) AS t5_odds
    FROM odds_trifecta
   WHERE combination='1-2-3'
     AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
   GROUP BY race_id
), l4 AS (
  SELECT r.race_id, t5.t5_odds AS fav_odds,
         res1.finishing_position AS b1_pos,
         pw.payout AS win_pay
    FROM races r
    JOIN race_entries e ON e.race_id=r.race_id AND e.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS nf FROM race_entries ef
                JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    JOIN t5 ON t5.race_id = r.race_id AND t5.any_in_l4 = 1
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.boat_number=1
    LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN {B}
     AND e.class_number = 1
     AND r.race_grade_number IN (1,2,3,4)
     AND (pv.weather_number IS NULL OR pv.weather_number != 3)
     AND COALESCE(fem.nf, 0) = 0
)
"""

# 1. 全体
print("=" * 68)
print("【1. L4 (男性のみ) 1号艇 単勝の損益分岐】")
print("=" * 68)
cur = conn.execute(base + """
SELECT COUNT(*) AS n,
       SUM(CASE WHEN b1_pos=1 THEN 1 ELSE 0 END) AS wins,
       AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END) AS win_rate,
       SUM(CASE WHEN b1_pos=1 THEN COALESCE(win_pay,0) ELSE 0 END)*1.0/(COUNT(*)*100) AS tansho_roi,
       AVG(CASE WHEN b1_pos=1 THEN win_pay END) AS avg_win_pay
  FROM l4
""")
n, wins, wr, roi, avgp = cur.fetchone()
wr = float(wr or 0); roi = float(roi or 0); avgp = float(avgp or 0)
breakeven = 1/wr if wr else 0
print(f"  対象: {n:,} レース, 1号艇1着 {wins:,} 回")
print(f"  1号艇 1着率 P = {wr*100:.2f}%")
print(f"  → 損益分岐オッズ = 1 / P = {breakeven:.3f}")
print(f"  実際に単勝を毎回買った時の ROI = {roi*100:.1f}% (平均的中払戻 ¥{avgp:.0f})")
print(f"  平均実現単勝オッズ (的中時) = {avgp/100:.2f}")

# 2. 3連単本命オッズ帯別
print()
print("=" * 68)
print("【2. 3連単本命オッズ帯別: 1着率と損益分岐オッズ】")
print("=" * 68)
cur = conn.execute(base + """
SELECT CASE WHEN fav_odds < 6 THEN '5-6倍'
            WHEN fav_odds < 7 THEN '6-7倍'
            WHEN fav_odds < 8 THEN '7-8倍'
            WHEN fav_odds < 9 THEN '8-9倍'
            WHEN fav_odds < 10 THEN '9-10倍'
            ELSE '不明' END AS band,
       COUNT(*) AS n,
       AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END) AS win_rate,
       AVG(CASE WHEN b1_pos=1 THEN win_pay END)/100.0 AS avg_odds,
       SUM(CASE WHEN b1_pos=1 THEN COALESCE(win_pay,0) ELSE 0 END)*1.0/(COUNT(*)*100) AS tansho_roi
  FROM l4 GROUP BY band ORDER BY band
""")
print(f'{"3連単本命帯":<10} {"n":>5} {"1着率":>7} {"分岐odds":>9} {"平均実現odds":>11} {"単勝ROI":>8}')
print("-" * 60)
for band, n, wr, ao, roi in cur.fetchall():
    wr = float(wr or 0); ao = float(ao or 0); roi = float(roi or 0)
    be = 1/wr if wr else 0
    print(f"{band:<10} {n:>5} {wr*100:>6.1f}% {be:>9.2f} {ao:>11.2f} {roi*100:>7.1f}%")

# 3. 実現単勝オッズ帯別の 単勝 ROI (的中レースのみ odds 既知の制約あり)
print()
print("=" * 68)
print("【3. 実現単勝オッズ帯別 (的中レースの payout から逆算)】")
print("=" * 68)
print("注: 単勝オッズは事前スナップショットが無いため、的中レースの確定")
print("    払戻から逆算。負けレースのオッズは不明なので参考値。")
cur = conn.execute(base + """
, won AS (SELECT win_pay/100.0 AS odds FROM l4 WHERE b1_pos=1 AND win_pay IS NOT NULL)
SELECT CASE WHEN odds < 1.2 THEN '1.0-1.19'
            WHEN odds < 1.4 THEN '1.2-1.39'
            WHEN odds < 1.6 THEN '1.4-1.59'
            WHEN odds < 1.8 THEN '1.6-1.79'
            WHEN odds < 2.0 THEN '1.8-1.99'
            ELSE '2.0+' END AS oband,
       COUNT(*) AS won_n, AVG(odds) AS avg_odds
  FROM won GROUP BY oband ORDER BY oband
""")
print(f'{"単勝odds帯":<12} {"的中数":>6} {"平均odds":>9}')
for ob, n, ao in cur.fetchall():
    print(f"{ob:<12} {n:>6} {float(ao or 0):>9.2f}")

# 4. 不偏推定: 朝予測 prob_first 0.65-0.85 (= L4 本命帯の事前シグナル) で
#    4年分の 1号艇 1着率を計測 (odds_trifecta より遥かに大きい標本)
print()
print("=" * 68)
print("【4. 不偏推定: 朝予測 prob_first 0.65-0.85 の 1号艇 1着率 (4年・大標本)】")
print("=" * 68)
cur = conn.execute(f"""
WITH cand AS (
  SELECT r.race_id, p.prob_first,
         res1.finishing_position AS b1_pos,
         pw.payout AS win_pay
    FROM races r
    JOIN race_entries e ON e.race_id=r.race_id AND e.boat_number=1
    JOIN predictions p ON p.race_id=r.race_id AND p.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS nf FROM race_entries ef
                JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.boat_number=1
    LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
   WHERE r.race_date >= '2022-01-01'
     AND r.stadium_number NOT IN {B}
     AND e.class_number = 1
     AND r.race_grade_number IN (1,2,3,4)
     AND (pv.weather_number IS NULL OR pv.weather_number != 3)
     AND COALESCE(fem.nf, 0) = 0
     AND p.prob_first >= 0.65 AND p.prob_first < 0.85
)
SELECT COUNT(*) AS n,
       AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END) AS win_rate,
       SUM(CASE WHEN b1_pos=1 THEN COALESCE(win_pay,0) ELSE 0 END)*1.0/(COUNT(*)*100) AS tansho_roi,
       AVG(CASE WHEN b1_pos=1 THEN win_pay END)/100.0 AS avg_odds
  FROM cand
""")
n, wr, roi, ao = cur.fetchone()
wr = float(wr or 0); roi = float(roi or 0); ao = float(ao or 0)
be = 1/wr if wr else 0
print(f"  対象: {n:,} レース")
print(f"  1号艇 1着率 P = {wr*100:.2f}%")
print(f"  → 損益分岐オッズ = 1/P = {be:.3f}")
print(f"  単勝 ROI (毎回購入) = {roi*100:.1f}% / 平均実現単勝オッズ = {ao:.2f}")
print()
print("  prob_first 帯別:")
cur = conn.execute(f"""
WITH cand AS (
  SELECT p.prob_first, res1.finishing_position AS b1_pos
    FROM races r
    JOIN race_entries e ON e.race_id=r.race_id AND e.boat_number=1
    JOIN predictions p ON p.race_id=r.race_id AND p.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS nf FROM race_entries ef
                JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.boat_number=1
   WHERE r.race_date >= '2022-01-01' AND r.stadium_number NOT IN {B}
     AND e.class_number=1 AND r.race_grade_number IN (1,2,3,4)
     AND (pv.weather_number IS NULL OR pv.weather_number != 3)
     AND COALESCE(fem.nf,0)=0 AND p.prob_first >= 0.65 AND p.prob_first < 0.85
)
SELECT CASE WHEN prob_first < 0.70 THEN '0.65-0.70'
            WHEN prob_first < 0.75 THEN '0.70-0.75'
            WHEN prob_first < 0.80 THEN '0.75-0.80'
            ELSE '0.80-0.85' END AS band,
       COUNT(*) AS n, AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END) AS wr
  FROM cand GROUP BY band ORDER BY band
""")
print(f'  {"prob帯":<12} {"n":>6} {"1着率":>7} {"分岐odds":>9}')
for band, n, wr in cur.fetchall():
    wr = float(wr or 0); be = 1/wr if wr else 0
    print(f'  {band:<12} {n:>6} {wr*100:>6.1f}% {be:>9.2f}')

# 5. 最大標本・不偏: A1 1号艇 (B除外/男性のみ/grade1-4, オッズ事後フィルタなし)
#    国1% を「本命の強さ」proxy として 1着率と単勝損益分岐を 4年で計測。
print()
print("=" * 68)
print("【5. 最大標本(不偏): A1 1号艇の 国1%別 1着率と単勝損益分岐 (4年)】")
print("=" * 68)
sql5 = f"""
WITH base AS (
  SELECT e.national_top_1_percent AS n1,
         res1.finishing_position AS b1_pos,
         pw.payout AS win_pay
    FROM races r
    JOIN race_entries e ON e.race_id=r.race_id AND e.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS nf FROM race_entries ef
                JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.boat_number=1
    LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
   WHERE r.race_date >= '2022-01-01' AND r.stadium_number NOT IN {B}
     AND e.class_number=1 AND r.race_grade_number IN (1,2,3,4)
     AND (pv.weather_number IS NULL OR pv.weather_number != 3)
     AND COALESCE(fem.nf,0)=0
)
SELECT band, COUNT(*) AS n,
       AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END) AS wr,
       SUM(CASE WHEN b1_pos=1 THEN COALESCE(win_pay,0) ELSE 0 END)*1.0/(COUNT(*)*100) AS roi,
       AVG(CASE WHEN b1_pos=1 THEN win_pay END)/100.0 AS avg_odds
  FROM (
    SELECT *, CASE WHEN n1 IS NULL THEN '不明'
                   WHEN n1 < 6 THEN '〜6.0'
                   WHEN n1 < 6.5 THEN '6.0-6.5'
                   WHEN n1 < 7 THEN '6.5-7.0'
                   WHEN n1 < 7.5 THEN '7.0-7.5'
                   ELSE '7.5+' END AS band
      FROM base
  ) x GROUP BY band ORDER BY band
"""
print(f'{"国1%帯":<10} {"n":>6} {"1着率":>7} {"分岐odds":>9} {"平均実現odds":>11} {"単勝ROI":>8}')
print("-" * 62)
for band, n, wr, roi, ao in conn.execute(sql5).fetchall():
    wr = float(wr or 0); roi = float(roi or 0); ao = float(ao or 0); be = 1/wr if wr else 0
    print(f'{band:<10} {n:>6,} {wr*100:>6.1f}% {be:>9.2f} {ao:>11.2f} {roi*100:>7.1f}%')

# 全体集計
cur = conn.execute(f"""
WITH base AS (
  SELECT res1.finishing_position AS b1_pos, pw.payout AS win_pay
    FROM races r
    JOIN race_entries e ON e.race_id=r.race_id AND e.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    LEFT JOIN (SELECT ef.race_id, COUNT(*) AS nf FROM race_entries ef
                JOIN racers rc ON rc.racer_number=ef.racer_number
               WHERE rc.gender=2 GROUP BY ef.race_id) fem ON fem.race_id=r.race_id
    LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.boat_number=1
    LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
   WHERE r.race_date >= '2022-01-01' AND r.stadium_number NOT IN {B}
     AND e.class_number=1 AND r.race_grade_number IN (1,2,3,4)
     AND (pv.weather_number IS NULL OR pv.weather_number != 3)
     AND COALESCE(fem.nf,0)=0
)
SELECT COUNT(*), AVG(CASE WHEN b1_pos=1 THEN 1.0 ELSE 0 END),
       SUM(CASE WHEN b1_pos=1 THEN COALESCE(win_pay,0) ELSE 0 END)*1.0/(COUNT(*)*100),
       AVG(CASE WHEN b1_pos=1 THEN win_pay END)/100.0
  FROM base
""")
n, wr, roi, ao = cur.fetchone()
wr = float(wr or 0); roi = float(roi or 0); ao = float(ao or 0)
print()
print(f"全体: n={n:,}, 1着率={wr*100:.1f}%, 損益分岐odds={1/wr:.2f}, "
      f"単勝ROI={roi*100:.1f}%, 平均実現単勝odds={ao:.2f}")
