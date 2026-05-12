"""
過去4年バックテスト (2022-05-08 ~ 2026-05-12)
単勝払戻を使った「降りる勇気」フィルタ検証

3連単本命払戻は2025-07以降のみなので、
代替として「1号艇単勝オッズ帯」で人気度を判定。

人気の定義 (1号艇単勝払戻 = 1号艇単勝オッズ × 100):
  100-150 円帯: 鉄板1号艇 (オッズ 1.0-1.5)
  150-200 円帯: 本命1号艇 (オッズ 1.5-2.0)
  200-300 円帯: やや本命 (オッズ 2.0-3.0)
  300-500 円帯: 中穴傾向 (オッズ 3.0-5.0)
  500 円以上: 1号艇難レース
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
# レース基本情報
cur = conn.execute("""
    SELECT race_id, stadium_number, race_date FROM races
    WHERE race_date >= '2022-05-08'
""")
race_info = {}
for rid, s, d in cur.fetchall():
    race_info[rid] = {"stadium": s, "date": d}
print(f"  races: {len(race_info):,}")

# 1着 + top3
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

# 1号艇単勝払戻 (人気度判定用)
cur = conn.execute("""
    SELECT race_id, payout FROM race_payouts
    WHERE bet_type='win' AND combination='1'
""")
boat1_win_pay = dict(cur.fetchall())
print(f"  boat1_win_payout: {len(boat1_win_pay):,}")

# 複勝 1号艇
cur = conn.execute("""
    SELECT race_id, payout FROM race_payouts
    WHERE bet_type='place' AND combination='1'
""")
boat1_place_pay = dict(cur.fetchall())
print(f"  boat1_place_payout: {len(boat1_place_pay):,}")

# 拡連複 1=2
cur = conn.execute("""
    SELECT race_id, combination, payout FROM race_payouts
    WHERE bet_type='quinella_place'
""")
qp_pay = defaultdict(dict)
for rid, combo, p in cur.fetchall():
    qp_pay[rid][combo] = p
print(f"  quinella_place: loaded")

# 1号艇クラス
cur = conn.execute("""
    SELECT race_id, class_number FROM race_entries WHERE boat_number = 1
""")
boat1_class = dict(cur.fetchall())
print(f"  boat1_class: {len(boat1_class):,}\n")

LOSING_VENUES = {2, 7, 10, 21}
QUESTIONABLE = {4, 8, 19, 24}
EXCLUDE_B = LOSING_VENUES | QUESTIONABLE

# 戦略: 1号艇単勝
def bet_win_boat1(rid):
    if winner.get(rid) == 1:
        return boat1_win_pay.get(rid, 0) or 0
    return 0

# 戦略: 1号艇複勝
def bet_place_boat1(rid):
    if rid in positions and 1 in [positions[rid].get(p) for p in (1,2,3)]:
        return boat1_place_pay.get(rid, 0) or 0
    return 0

# 戦略: 拡連複 1=2 (1号艇と2号艇が両方とも3着以内)
def bet_qp_12(rid):
    top3 = set(positions[rid].values())
    if 1 in top3 and 2 in top3:
        return qp_pay[rid].get('1=2', 0) or qp_pay[rid].get('1-2', 0) or 0
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
    mark = "[150+]" if recovery >= 150 else "[140+]" if recovery >= 140 else "[130+]" if recovery >= 130 else "[120+]" if recovery >= 120 else ""
    print(f"  {name:<60} n={n:>7,} HIT{n_hit/n:>5.1%} 回収{recovery:>6.1f}% "
          f"CI[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] P+{p0:>5.1%} 損益{profit:>+12,}円 {mark}")

def in_band(rid, lo, hi):
    """1号艇単勝オッズ帯 (lo <= payout < hi)"""
    p = boat1_win_pay.get(rid)
    return p is not None and lo <= p < hi

print("=" * 130)
print("4年バックテスト (2022-05-08 ~ 2026-05-12) ~ 47,000 レース x 6 艇")
print("帯域は1号艇単勝払戻で判定 (例: 150円帯 = 1号艇単勝オッズ1.5倍)")
print("=" * 130)

print("\n[基準] 1号艇単勝 全レース")
analyze("全レース 1号艇単勝", lambda r,i: True, bet_win_boat1)

print("\n[Level 0] オッズ帯域別 1号艇単勝 (フィルタ無し)")
for lo, hi in [(100,120),(120,150),(150,200),(200,300),(300,500),(500,1000),(1000,99999)]:
    label = f"単勝払戻 {lo}-{hi if hi<99999 else 'inf'}円"
    analyze(f"L0 {label} 1号艇単勝", lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi), bet_win_boat1)

print("\n[Level 1] 損切除外 (戸田/蒲郡/三国/芦屋)")
for lo, hi in [(120,150),(150,200),(200,300),(300,500)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L1 {label} 単勝 - 損切除外",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in LOSING_VENUES, bet_win_boat1)

print("\n[Level 2] 損切+怪しい除外 (+平和島/常滑/下関/大村)")
for lo, hi in [(120,150),(150,200),(200,300),(300,500)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L2 {label} 単勝 - B除外",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B, bet_win_boat1)

print("\n[Level 3] L2 + クラスフィルタ A1A2")
for lo, hi in [(120,150),(150,200),(200,300)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L3 {label} 単勝 - B除外 - A1A2",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B
            and boat1_class.get(r) in (1,2), bet_win_boat1)

print("\n[Level 4] L2 + クラスフィルタ A1のみ (超厳選)")
for lo, hi in [(120,150),(150,200),(200,300)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L4 {label} 単勝 - B除外 - A1",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B
            and boat1_class.get(r) == 1, bet_win_boat1)

print("\n[Level 5] B1 限定 オッズ甘いゾーン狙い")
for lo, hi in [(150,200),(200,300),(300,500)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L5 {label} 単勝 - B除外 - B1",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B
            and boat1_class.get(r) == 3, bet_win_boat1)

print("\n[Level 6] 複勝戦略 (低分散)")
for lo, hi in [(120,150),(150,200),(200,300)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L6 {label} 複勝 - B除外 - A1A2",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B
            and boat1_class.get(r) in (1,2), bet_place_boat1)

print("\n[Level 7] 拡連複 1=2 (1号艇+2号艇が3着以内)")
for lo, hi in [(150,200),(200,300),(300,500)]:
    label = f"単勝払戻 {lo}-{hi}円"
    analyze(f"L7 {label} 拡連複 1=2 - B除外 - A1A2",
        lambda r,i,lo=lo,hi=hi: in_band(r,lo,hi) and i["stadium"] not in EXCLUDE_B
            and boat1_class.get(r) in (1,2), bet_qp_12)

print("\n[年別検証] L2 200-300円帯 単勝 - B除外 (年ごとの安定性)")
for year in ["2022","2023","2024","2025","2026"]:
    bets = []
    for rid, info in race_info.items():
        if not info["date"].startswith(year): continue
        if rid not in winner: continue
        if not in_band(rid, 200, 300): continue
        if info["stadium"] in EXCLUDE_B: continue
        bets.append(bet_win_boat1(rid))
    if not bets:
        print(f"  {year}: n=0")
        continue
    n_hit = sum(1 for b in bets if b > 0)
    rec = sum(bets) / len(bets)
    lo, hi, p0 = bootstrap_ci(bets)
    print(f"  {year}: n={len(bets):>5,} HIT{n_hit/len(bets):>5.1%} 回収{rec:>6.1f}% CI[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] P+{p0:>5.1%}")

print("\n[ポートフォリオ] 全帯域単勝合成 (B除外, A1A2)")
all_bets = []
for rid, info in race_info.items():
    if rid not in winner: continue
    p = boat1_win_pay.get(rid)
    if p is None or p < 120 or p >= 300: continue
    if info["stadium"] in EXCLUDE_B: continue
    if boat1_class.get(rid) not in (1, 2): continue
    all_bets.append(bet_win_boat1(rid))
n = len(all_bets)
n_hit = sum(1 for b in all_bets if b > 0)
total_pay = sum(all_bets)
rec = total_pay / (100 * n) * 100
lo, hi, p0 = bootstrap_ci(all_bets)
profit = total_pay - 100 * n
print(f"  全帯域(120-300円)単勝 - B除外 - A1A2: n={n:,} HIT{n_hit/n:.1%} 回収{rec:.1f}% "
      f"CI[{lo*100+100:.1f},{hi*100+100:.1f}] P+{p0:.1%} 損益{profit:+,}円")

print("\n[詳細マトリクス] 単勝オッズ帯 x クラス x B除外 単勝回収率")
print(f"  {'帯域':<14} {'A1':>15} {'A2':>15} {'B1':>15} {'B2':>15} {'全クラス':>15}")
for lo_p, hi_p in [(100,120),(120,150),(150,200),(200,300),(300,500),(500,1000),(1000,99999)]:
    label = f"{lo_p}-{hi_p if hi_p<99999 else 'inf'}"
    line = f"  {label:<14}"
    for cls in [1, 2, 3, 4, 0]:
        bets = []
        for rid, info in race_info.items():
            if rid not in winner: continue
            if not in_band(rid, lo_p, hi_p): continue
            if info["stadium"] in EXCLUDE_B: continue
            if cls != 0 and boat1_class.get(rid) != cls: continue
            bets.append(bet_win_boat1(rid))
        if not bets:
            line += f" {'-':>14}"
            continue
        rec = sum(bets) / len(bets)
        line += f" {rec:>7.1f}%n={len(bets):>5,}"
    print(line)
