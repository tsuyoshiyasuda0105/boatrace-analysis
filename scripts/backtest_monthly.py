"""
10ヶ月の月別バックテスト (2025-07 ~ 2026-05)

戦略: 3連単本命払戻ベース + 会場 + クラス フィルタ
これまでの L0~L7 戦略を各月で分解し、時期による安定性を検証
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
    SELECT r.race_id, r.stadium_number, r.race_date,
           MIN(pp.payout) as fav_payout
    FROM races r
    JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type='trifecta'
    GROUP BY r.race_id
""")
race_info = {}
for rid, s, d, mp in cur.fetchall():
    if mp:
        race_info[rid] = {"stadium": s, "date": d, "fav_payout": mp}
print(f"  race_info: {len(race_info):,}")

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

cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
pays = defaultdict(dict)
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p

cur = conn.execute("SELECT race_id, class_number FROM race_entries WHERE boat_number=1")
boat1_class = dict(cur.fetchall())
print()

LOSING_VENUES = {2, 7, 10, 21}
QUESTIONABLE = {4, 8, 19, 24}
EXCLUDE_B = LOSING_VENUES | QUESTIONABLE

def bet_win_boat1(rid):
    return pays[rid].get(('win', '1'), 0) or 0 if winner.get(rid) == 1 else 0

def bet_exacta_12(rid):
    if positions[rid].get(1) == 1 and positions[rid].get(2) == 2:
        return pays[rid].get(('exacta', '1-2'), 0) or 0
    return 0

def bet_trifecta_123(rid):
    if (positions[rid].get(1) == 1 and positions[rid].get(2) == 2
        and positions[rid].get(3) == 3):
        return pays[rid].get(('trifecta', '1-2-3'), 0) or 0
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

def monthly_run(name, filter_fn, bet_fn):
    """月別に分解"""
    by_month = defaultdict(list)
    for rid, info in race_info.items():
        if rid not in winner: continue
        if not filter_fn(rid, info): continue
        ym = info["date"][:7]  # YYYY-MM
        by_month[ym].append(bet_fn(rid))
    print(f"\n=== {name} ===")
    print(f"  {'月':<10} {'n':>5} {'HIT':>6} {'回収':>8} {'CI 95%':>20} {'P+':>6} {'損益':>10}")
    total_bets = []
    for ym in sorted(by_month):
        bets = by_month[ym]
        n = len(bets)
        n_hit = sum(1 for b in bets if b > 0)
        rec = sum(bets) / (100 * n) * 100 if n else 0
        profit = sum(bets) - 100 * n
        lo, hi, p0 = bootstrap_ci(bets) if n else (0,0,0)
        mark = "[150+]" if rec >= 150 else "[130+]" if rec >= 130 else ""
        if lo is not None:
            print(f"  {ym:<10} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% "
                  f"[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] {p0:>5.1%} {profit:>+9,}円 {mark}")
        total_bets.extend(bets)
    # 通算
    n = len(total_bets)
    n_hit = sum(1 for b in total_bets if b > 0)
    rec = sum(total_bets) / (100 * n) * 100
    profit = sum(total_bets) - 100 * n
    lo, hi, p0 = bootstrap_ci(total_bets)
    print(f"  {'---通算---':<10} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% "
          f"[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] {p0:>5.1%} {profit:>+9,}円")

# 主要戦略
monthly_run("L0 500-1000帯 3連単1-2-3 (フィルタ無)",
    lambda r,i: 500 <= i["fav_payout"] < 1000, bet_trifecta_123)
monthly_run("L2 500-1000帯 3連単1-2-3 + B除外",
    lambda r,i: (500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B),
    bet_trifecta_123)
monthly_run("L3 500-1000帯 3連単1-2-3 + B除外 + A1A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_trifecta_123)
monthly_run("[★最強] L4 500-1000帯 3連単1-2-3 + B除外 + A1",
    lambda r,i: (500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 1), bet_trifecta_123)
monthly_run("L2 1000-2000帯 単勝 + B除外",
    lambda r,i: (1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B),
    bet_win_boat1)
monthly_run("L2 500-1000帯 2連単1-2 + B除外",
    lambda r,i: (500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B),
    bet_exacta_12)

# 多戦略ポートフォリオ 月別
print("\n=== [ポートフォリオ] 単勝500-1000 + 単勝1000-2000 + 2連単500-1000 (B除外) ===")
print(f"  {'月':<10} {'n':>5} {'HIT':>6} {'回収':>8} {'損益':>10}")
strategies = [
    (lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1),
    (lambda r,i: 1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1),
    (lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_exacta_12),
]
by_month_combined = defaultdict(list)
for rid, info in race_info.items():
    if rid not in winner: continue
    ym = info["date"][:7]
    for f, b in strategies:
        if f(rid, info):
            by_month_combined[ym].append(b(rid))
total_combo = []
for ym in sorted(by_month_combined):
    bets = by_month_combined[ym]
    n = len(bets)
    n_hit = sum(1 for b in bets if b > 0)
    rec = sum(bets) / (100 * n) * 100
    profit = sum(bets) - 100 * n
    mark = "[150+]" if rec >= 150 else "[130+]" if rec >= 130 else ""
    print(f"  {ym:<10} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% {profit:>+9,}円 {mark}")
    total_combo.extend(bets)
n = len(total_combo)
n_hit = sum(1 for b in total_combo if b > 0)
rec = sum(total_combo) / (100 * n) * 100
profit = sum(total_combo) - 100 * n
print(f"  {'---通算---':<10} {n:>5,} {n_hit/n:>5.1%} {rec:>7.1f}% {profit:>+9,}円")
