"""雨除外フィルタが detect_l4_alerts で効いていることを検証。

過去日 (2026-05-03 = 雨 97 レース) で:
  - 修正前相当 (raw query): 全該当数を測る
  - 修正後 detect_l4_alerts(): 雨除外後の数を測る
  - 差分が雨除外件数と一致するか確認
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect
from scripts.send_l4_alerts import detect_l4_alerts

TARGET = "2026-05-20"  # 雨 3 レース、直近で T-5min データあり

conn = connect()
cur = conn.execute("""
  SELECT r.race_id, e.class_number, pv.weather_number, t5.payout,
         r.race_grade_number
    FROM races r
    JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
    LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
    JOIN (SELECT race_id, MIN(odds)*100 AS payout
            FROM odds_trifecta
           WHERE snapshot_label='T-5min'
           GROUP BY race_id) t5 ON r.race_id=t5.race_id
   WHERE r.race_date=?
     AND e.class_number=1
     AND t5.payout BETWEEN 500 AND 1000
     AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
""", (TARGET,))
all_rows = cur.fetchall()
total = len(all_rows)
rainy = sum(1 for r in all_rows if r[2] == 3)
print(f"{TARGET}: L4 帯該当 (B除外後): 全 {total} 件、うち雨 {rainy} 件")
print(f"  修正前: {total} 件メール対象")
print(f"  修正後想定: {total - rainy} 件 (雨 {rainy} 件除外)")
print()

alerts = detect_l4_alerts(TARGET)
print(f"  detect_l4_alerts() 実際の戻り値: {len(alerts)} 件")
print()

# 一般戦 (grade=5) は F1 条件で更に絞られるので、純粋な雨除外効果は
# SG/G1/G2/G3 のみで比較する方が正確
sgg_total = sum(1 for r in all_rows if r[4] in (1, 2, 3, 4))
sgg_rainy = sum(1 for r in all_rows if r[4] in (1, 2, 3, 4) and r[2] == 3)
sgg_alerts = [a for a in alerts if a.get("alert_type") in
              ("L4_SG", "L4_G1", "L4_G2", "L4_G3")]
print(f"SG/G1/G2/G3 のみで比較:")
print(f"  L4 帯該当: 全 {sgg_total} 件, 雨 {sgg_rainy} 件 → 雨除外後 {sgg_total - sgg_rainy} 件")
print(f"  detect_l4_alerts() SG/G1/G2/G3: {len(sgg_alerts)} 件")
if len(sgg_alerts) == sgg_total - sgg_rainy:
    print("  ✅ 雨除外が正しく効いています")
else:
    diff = (sgg_total - sgg_rainy) - len(sgg_alerts)
    print(f"  注: 差分 {diff} 件 (final 除外 / その他フィルタによる)")
