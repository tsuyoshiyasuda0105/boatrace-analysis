"""
L4 戦略のレースグレード別の細分検証

L4 = 三連単本命払戻 500-1000円 + B除外 + 1号艇A1 + 3連単1-2-3買い

レースグレード (race_grade_number) で更に分解:
  0 = 一般戦 (大半)
  1 = G3
  2 = G2
  3 = G1
  4 = SG
"""
import sys
import sqlite3
import random
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
random.seed(42)
N_BOOT = 1000

conn = sqlite3.connect('data/boatrace.db')

cur = conn.execute("""
    SELECT r.race_id, r.stadium_number, r.race_grade_number,
           MIN(pp.payout) as fav_payout
    FROM races r
    JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type='trifecta'
    GROUP BY r.race_id
""")
race_info = {}
for rid, s, g, mp in cur.fetchall():
    if mp:
        race_info[rid] = {"stadium": s, "grade": g, "fav_payout": mp}

cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position FROM race_results
    WHERE finishing_position IN (1,2,3)
""")
positions = defaultdict(dict)
winner = {}
for rid, bn, pos in cur.fetchall():
    positions[rid][pos] = bn
    if pos == 1: winner[rid] = bn

cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
pays = defaultdict(dict)
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p

cur = conn.execute("SELECT race_id, class_number FROM race_entries WHERE boat_number=1")
boat1_class = dict(cur.fetchall())

LOSING_VENUES = {2, 7, 10, 21}
QUESTIONABLE = {4, 8, 19, 24}
EXCLUDE_B = LOSING_VENUES | QUESTIONABLE

def bet_trifecta_123(rid):
    if (positions[rid].get(1) == 1 and positions[rid].get(2) == 2
        and positions[rid].get(3) == 3):
        return pays[rid].get(('trifecta', '1-2-3'), 0) or 0
    return 0

def bet_win_boat1(rid):
    return pays[rid].get(('win', '1'), 0) or 0 if winner.get(rid) == 1 else 0

def bet_exacta_12(rid):
    if positions[rid].get(1) == 1 and positions[rid].get(2) == 2:
        return pays[rid].get(('exacta', '1-2'), 0) or 0
    return 0

def bootstrap_ci(payouts):
    n = len(payouts)
    if n == 0: return None, None, None
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rois.append((sum(sample)/n - 100) / 100)
    rois.sort()
    return (rois[int(N_BOOT*0.025)], rois[int(N_BOOT*0.975)],
            sum(1 for r in rois if r > 0) / N_BOOT)

GRADE_NAMES = {0: "一般戦", 1: "G3", 2: "G2", 3: "G1", 4: "SG"}

def analyze_by_grade(name, bet_fn, class_filter):
    print(f"\n=== {name} ===")
    print(f"  {'グレード':<8} {'n':>5} {'HIT':>6} {'回収':>8} {'CI 95%':>20} {'P+':>6} {'損益':>10}")
    total_bets = []
    for g_val in [0, 1, 2, 3, 4, None]:
        bets = []
        for rid, info in race_info.items():
            if rid not in winner: continue
            if not (500 <= info["fav_payout"] < 1000): continue
            if info["stadium"] in EXCLUDE_B: continue
            if class_filter and boat1_class.get(rid) not in class_filter: continue
            if g_val is None:
                if info["grade"] is not None: continue
            else:
                if info["grade"] != g_val: continue
            bets.append(bet_fn(rid))
        n = len(bets)
        if n == 0: continue
        n_hit = sum(1 for b in bets if b > 0)
        rec = sum(bets) / (100 * n) * 100
        profit = sum(bets) - 100 * n
        lo, hi, p0 = bootstrap_ci(bets)
        label = GRADE_NAMES.get(g_val, "未設定")
        mark = "[150+]" if rec >= 150 else "[130+]" if rec >= 130 else ""
        print(f"  {label:<8} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% "
              f"[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] {p0:>5.1%} {profit:>+9,}円 {mark}")
        total_bets.extend(bets)
    n = len(total_bets)
    if n:
        n_hit = sum(1 for b in total_bets if b > 0)
        rec = sum(total_bets) / (100 * n) * 100
        profit = sum(total_bets) - 100 * n
        lo, hi, p0 = bootstrap_ci(total_bets)
        print(f"  {'通算':<8} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% "
              f"[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] {p0:>5.1%} {profit:>+9,}円")

print("=" * 100)
print("L4 戦略 (500-1000帯 + B除外 + 3連単1-2-3) のグレード×クラス分解")
print("=" * 100)

analyze_by_grade("L4 [A1のみ] 3連単1-2-3", bet_trifecta_123, {1})
analyze_by_grade("L4 [A2のみ] 3連単1-2-3", bet_trifecta_123, {2})
analyze_by_grade("L4 [A1A2] 3連単1-2-3", bet_trifecta_123, {1, 2})
analyze_by_grade("L4 [B1のみ] 3連単1-2-3", bet_trifecta_123, {3})
analyze_by_grade("L4 [クラス不問] 3連単1-2-3", bet_trifecta_123, None)

print("\n--- 参考: 同条件で 単勝 / 2連単 はどうか ---")
analyze_by_grade("[A1のみ] 1号艇単勝", bet_win_boat1, {1})
analyze_by_grade("[A1のみ] 2連単1-2", bet_exacta_12, {1})

# A1 + 一般戦の詳細
print("\n--- 詳細: A1 + 一般戦 + B除外 + 500-1000帯 ---")
target = [rid for rid, info in race_info.items()
          if rid in winner
          and 500 <= info["fav_payout"] < 1000
          and info["stadium"] not in EXCLUDE_B
          and boat1_class.get(rid) == 1
          and (info["grade"] or 0) == 0]
n = len(target)
print(f"  該当レース: {n:,}")
print(f"  3連単1-2-3 ヒット: {sum(1 for r in target if bet_trifecta_123(r) > 0):,}")
print(f"  単勝 ヒット: {sum(1 for r in target if bet_win_boat1(r) > 0):,}")
print(f"  2連単1-2 ヒット: {sum(1 for r in target if bet_exacta_12(r) > 0):,}")
