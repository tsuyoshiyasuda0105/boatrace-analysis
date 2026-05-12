"""
過去4年バックテスト (2022-05-08 ~ 2026-05-12) - selection bias 修正版

修正点:
  - 単勝払戻は 1号艇が1着になったレースしか記録されない (post-race data)
  - そこで「払戻帯域でレースを分類」はできない (生存者バイアス)
  - 代わりに「会場 + 1号艇クラス + race_grade」の事前情報のみでフィルタ
  - 母集団は「全レース」、勝った場合のみ payout を加算

期待:
  全レース 1号艇単勝 = 90% 回収 (基準)
  フィルタを足すと どれだけ上振れるか
"""
import sys
import sqlite3
import random
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
random.seed(42)
N_BOOT = 1000

conn = sqlite3.connect('data/boatrace.db')

print("データロード中...")
cur = conn.execute("""
    SELECT race_id, stadium_number, race_date, race_grade_number FROM races
    WHERE race_date >= '2022-05-08'
""")
race_info = {}
for rid, s, d, g in cur.fetchall():
    race_info[rid] = {"stadium": s, "date": d, "grade": g}
print(f"  races: {len(race_info):,}")

cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position FROM race_results
    WHERE finishing_position IN (1,2,3)
""")
positions = defaultdict(dict)
winner = {}
for rid, bn, pos in cur.fetchall():
    positions[rid][pos] = bn
    if pos == 1:
        winner[rid] = bn
print(f"  winner: {len(winner):,}")

# 1号艇単勝払戻 (1号艇が1着のレースだけ)
cur = conn.execute("""
    SELECT race_id, payout FROM race_payouts
    WHERE bet_type='win' AND combination='1'
""")
boat1_win_pay = dict(cur.fetchall())
print(f"  boat1_win_pay: {len(boat1_win_pay):,} (1号艇1着のレースのみ)")

cur = conn.execute("""
    SELECT race_id, payout FROM race_payouts
    WHERE bet_type='place' AND combination='1'
""")
boat1_place_pay = dict(cur.fetchall())
print(f"  boat1_place_pay: {len(boat1_place_pay):,}")

# 1号艇クラス
cur = conn.execute("""
    SELECT race_id, class_number FROM race_entries WHERE boat_number = 1
""")
boat1_class = dict(cur.fetchall())
print(f"  boat1_class: {len(boat1_class):,}\n")

LOSING_VENUES = {2, 7, 10, 21}
QUESTIONABLE = {4, 8, 19, 24}
EXCLUDE_B = LOSING_VENUES | QUESTIONABLE

def bet_win_boat1(rid):
    if winner.get(rid) == 1:
        return boat1_win_pay.get(rid, 0) or 0
    return 0

def bet_place_boat1(rid):
    top3 = set(positions[rid].values())
    if 1 in top3:
        return boat1_place_pay.get(rid, 0) or 0
    return 0

def bootstrap_ci(payouts):
    n = len(payouts)
    if n == 0:
        return None, None, None
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rois.append((sum(sample) / n - 100) / 100)
    rois.sort()
    return (rois[int(N_BOOT * 0.025)],
            rois[int(N_BOOT * 0.975)],
            sum(1 for r in rois if r > 0) / N_BOOT)

def analyze(name, filter_fn, bet_fn):
    bets = []
    for rid, info in race_info.items():
        if rid not in winner:
            continue
        if filter_fn(rid, info):
            bets.append(bet_fn(rid))
    n = len(bets)
    if n == 0:
        print(f"  {name:<60} n=0")
        return
    n_hit = sum(1 for b in bets if b > 0)
    total_bet = 100 * n
    total_pay = sum(bets)
    recovery = total_pay / total_bet * 100
    lo, hi, p0 = bootstrap_ci(bets)
    profit = total_pay - total_bet
    mark = "[150+]" if recovery >= 150 else "[130+]" if recovery >= 130 else "[110+]" if recovery >= 110 else ""
    print(f"  {name:<60} n={n:>7,} HIT{n_hit/n:>5.1%} 回収{recovery:>6.1f}% "
          f"CI[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] P+{p0:>5.1%} 損益{profit:>+12,}円 {mark}")

# Grade 数値: 0=一般戦, 1=G3, 2=G2, 3=G1, 4=SG (推定)
GENERAL_RACE = 0  # 一般戦

print("=" * 130)
print("4年バックテスト (2022-05-08 ~ 2026-05-12) ~ 210,000 レース")
print("全レースが母集団 (1号艇が負けた場合は payout=0、生存者バイアスなし)")
print("=" * 130)

print("\n[基準] フィルタ無し")
analyze("L0 全レース 1号艇単勝", lambda r,i: True, bet_win_boat1)
analyze("L0 全レース 1号艇複勝", lambda r,i: True, bet_place_boat1)

print("\n[Level 1] 損切除外 (戸田/蒲郡/三国/芦屋)")
analyze("L1 1号艇単勝 - 損切除外",
    lambda r,i: i["stadium"] not in LOSING_VENUES, bet_win_boat1)

print("\n[Level 2] 損切+怪しい除外 (+平和島/常滑/下関/大村)")
analyze("L2 1号艇単勝 - B除外",
    lambda r,i: i["stadium"] not in EXCLUDE_B, bet_win_boat1)
analyze("L2 1号艇複勝 - B除外",
    lambda r,i: i["stadium"] not in EXCLUDE_B, bet_place_boat1)

print("\n[Level 3] L2 + クラスフィルタ")
analyze("L3 1号艇単勝 - B除外 - A1",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1, bet_win_boat1)
analyze("L3 1号艇単勝 - B除外 - A2",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 2, bet_win_boat1)
analyze("L3 1号艇単勝 - B除外 - B1",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 3, bet_win_boat1)
analyze("L3 1号艇単勝 - B除外 - A1A2",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) in (1,2), bet_win_boat1)

print("\n[Level 4] グレード別 1号艇単勝 (B除外)")
analyze("L4 一般戦 - B除外 - A1A2",
    lambda r,i: i["stadium"] not in EXCLUDE_B and (i["grade"] or 0) == 0 and boat1_class.get(r) in (1,2), bet_win_boat1)
analyze("L4 一般戦 - B除外 - B1",
    lambda r,i: i["stadium"] not in EXCLUDE_B and (i["grade"] or 0) == 0 and boat1_class.get(r) == 3, bet_win_boat1)
analyze("L4 G3+ - B除外 - A1A2",
    lambda r,i: i["stadium"] not in EXCLUDE_B and (i["grade"] or 0) >= 1 and boat1_class.get(r) in (1,2), bet_win_boat1)

print("\n[Level 5] 厳選: B除外 + 一般戦 + 特定クラス")
analyze("L5 B除外 一般戦 A1A2 単勝",
    lambda r,i: (i["stadium"] not in EXCLUDE_B and (i["grade"] or 0) == 0
        and boat1_class.get(r) in (1,2)), bet_win_boat1)
analyze("L5 B除外 一般戦 A1A2 複勝",
    lambda r,i: (i["stadium"] not in EXCLUDE_B and (i["grade"] or 0) == 0
        and boat1_class.get(r) in (1,2)), bet_place_boat1)

print("\n[Level 6] 複勝戦略 (低分散)")
analyze("L6 1号艇複勝 - B除外 - A1A2",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) in (1,2), bet_place_boat1)
analyze("L6 1号艇複勝 - B除外 - A1",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1, bet_place_boat1)
analyze("L6 1号艇複勝 - B除外 - B1",
    lambda r,i: i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 3, bet_place_boat1)

print("\n[年別検証] L3 1号艇単勝 - B除外 - A1A2 (年ごとの安定性)")
for year in ["2022","2023","2024","2025","2026"]:
    bets = []
    for rid, info in race_info.items():
        if not info["date"].startswith(year): continue
        if rid not in winner: continue
        if info["stadium"] in EXCLUDE_B: continue
        if boat1_class.get(rid) not in (1, 2): continue
        bets.append(bet_win_boat1(rid))
    if not bets:
        print(f"  {year}: n=0")
        continue
    n_hit = sum(1 for b in bets if b > 0)
    rec = sum(bets) / len(bets)
    lo, hi, p0 = bootstrap_ci(bets)
    profit = sum(bets) - 100 * len(bets)
    print(f"  {year}: n={len(bets):>5,} HIT{n_hit/len(bets):>5.1%} 回収{rec:>6.1f}% CI[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] P+{p0:>5.1%} 損益{profit:>+10,}円")

print("\n[詳細] 会場別 1号艇単勝 回収率 (4年合計、各会場で全レース)")
print(f"  {'会場#':>4} {'会場名':<8} {'n':>6} {'1着率':>7} {'回収率':>7}")
cur = conn.execute("SELECT stadium_number, name FROM stadiums")
stadium_names = {n: name for n, name in cur.fetchall()}
stadium_stats = []
for s_no, s_name in sorted(stadium_names.items()):
    bets = []
    for rid, info in race_info.items():
        if info["stadium"] != s_no: continue
        if rid not in winner: continue
        bets.append(bet_win_boat1(rid))
    if not bets: continue
    n_hit = sum(1 for b in bets if b > 0)
    rec = sum(bets) / len(bets)
    stadium_stats.append((s_no, s_name, len(bets), n_hit/len(bets), rec))
# 回収率順
stadium_stats.sort(key=lambda x: -x[4])
for s_no, s_name, n, hit, rec in stadium_stats:
    mark = "★" if rec >= 100 else "△" if rec >= 95 else "×"
    print(f"  {s_no:>4} {s_name:<6} {n:>6,} {hit:>6.1%} {rec:>6.1f}% {mark}")
