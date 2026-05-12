"""
1レース1000円ベット時の年間シミュレーション
10ヶ月実績 (2025-07~2026-05) から年換算 (x 12/10)

戦略別 + 税金シミュレーション + 心理的リスク評価
"""
import sys
import sqlite3
import random
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
random.seed(42)

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

GRADE_NAMES = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦"}

def collect(filter_fn, bet_fn):
    bets = []
    for rid, info in race_info.items():
        if rid not in winner: continue
        if filter_fn(rid, info):
            bets.append(bet_fn(rid))
    return bets

# 各戦略の10ヶ月実績 (1点100円基準)
strategies = [
    ("L4 SG x A1 (3連単1-2-3)",
        lambda r,i: (i["grade"] == 1 and 500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L4 G1 x A1 (3連単1-2-3)",
        lambda r,i: (i["grade"] == 2 and 500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L4 G2 x A1 (3連単1-2-3)",
        lambda r,i: (i["grade"] == 3 and 500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L4 G3 x A1 (3連単1-2-3)",
        lambda r,i: (i["grade"] == 4 and 500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L4 一般戦 x A1 (3連単1-2-3)",
        lambda r,i: (i["grade"] == 5 and 500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L4 [A1] 通算 (3連単1-2-3)",
        lambda r,i: (500 <= i["fav_payout"] < 1000
            and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
        bet_trifecta_123),
    ("L2 1000-2000帯 単勝 (B除外)",
        lambda r,i: (1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B),
        bet_win_boat1),
    ("L2 500-1000帯 2連単1-2 (B除外)",
        lambda r,i: (500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B),
        bet_exacta_12),
]

# 10ヶ月→年換算係数
ANNUAL_FACTOR = 12.0 / 10.0
BET_PER_RACE = 1000  # 1点1000円

def tax_calc(profit_yen, winning_bets_yen, recovery_yen):
    """
    一時所得課税 (実利益試算)
    一時所得 = 総回収 - 当たり券購入額 - 50万円控除
    課税対象 = 一時所得 × 1/2
    所得税+住民税 (累進):
      195万以下: 15%
      330万以下: 20%
      695万以下: 30%
      900万以下: 33%
    """
    if profit_yen <= 0:
        return 0, profit_yen
    ichiji = recovery_yen - winning_bets_yen - 500000
    if ichiji <= 0:
        return 0, profit_yen
    taxable = ichiji / 2
    if taxable <= 1950000:
        rate = 0.15
    elif taxable <= 3300000:
        rate = 0.20
    elif taxable <= 6950000:
        rate = 0.30
    elif taxable <= 9000000:
        rate = 0.33
    elif taxable <= 18000000:
        rate = 0.43
    else:
        rate = 0.50
    tax = int(taxable * rate)
    return tax, profit_yen - tax

print("=" * 130)
print("1レース1000円ベット時の年間シミュレーション (10ヶ月実績を年換算)")
print("=" * 130)
print(f"{'戦略':<40} {'年n':>6} {'HIT':>6} {'回収':>8} {'年投資':>10} {'年回収':>11} {'年利益':>11} {'税':>9} {'税引後':>11}")
print("-" * 130)

for name, f, b in strategies:
    bets = collect(f, b)
    if not bets: continue
    n_10mo = len(bets)
    n_year = int(n_10mo * ANNUAL_FACTOR)
    n_hit_10mo = sum(1 for x in bets if x > 0)
    hit_rate = n_hit_10mo / n_10mo
    n_hit_year = int(n_hit_10mo * ANNUAL_FACTOR)
    # 100円ベース → 1000円スケール (x10)
    recovery_rate = sum(bets) / (100 * n_10mo)
    annual_bet = n_year * BET_PER_RACE
    annual_recovery = int(annual_bet * recovery_rate)
    annual_profit = annual_recovery - annual_bet
    winning_bets_yen = n_hit_year * BET_PER_RACE
    tax, net = tax_calc(annual_profit, winning_bets_yen, annual_recovery)
    mark = "[150+]" if recovery_rate >= 1.5 else "[130+]" if recovery_rate >= 1.3 else ""
    print(f"{name:<40} {n_year:>6,} {hit_rate:>5.1%} {recovery_rate*100:>7.1f}% "
          f"{annual_bet:>8,}円 {annual_recovery:>9,}円 "
          f"{annual_profit:>+10,}円 {tax:>+8,}円 {net:>+10,}円 {mark}")

print()
print("=" * 130)
print("[推奨] ポートフォリオ A: L4 (G1+一般戦+その他全A1) のみで通年運用")
print("=" * 130)
bets = collect(
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
    bet_trifecta_123)
n_10 = len(bets); n_y = int(n_10*ANNUAL_FACTOR)
n_hit = sum(1 for x in bets if x>0); n_hit_y = int(n_hit*ANNUAL_FACTOR)
rec_rate = sum(bets) / (100*n_10)
ab = n_y * BET_PER_RACE; ar = int(ab*rec_rate); ap = ar - ab
tax, net = tax_calc(ap, n_hit_y*BET_PER_RACE, ar)
print(f"  年レース数:     {n_y:,} レース")
print(f"  HIT率:        {n_hit/n_10:.1%} ({n_hit_y:,} 的中 / {n_y - n_hit_y:,} 外れ)")
print(f"  年回収率:     {rec_rate*100:.1f}%")
print(f"  年投資総額:   {ab:,} 円")
print(f"  年回収総額:   {ar:,} 円")
print(f"  年利益 (税前): {ap:+,} 円")
print(f"  税金:        {tax:,} 円")
print(f"  税引後利益:   {net:+,} 円")
print(f"  実効回収率:   {(net+ab)/ab*100:.1f}% (税引後)")
# 月別変動シミュレーション
print()
print("  --- 月別変動シミュレーション (ブロックブートストラップ) ---")
random.seed(1)
month_results = []
SIMS = 100
for _ in range(SIMS):
    sim_bets = random.choices(bets, k=int(n_y/12))
    rec_m = sum(sim_bets) / (100 * len(sim_bets))
    month_results.append(rec_m)
month_results.sort()
mn = month_results[5]   # 5%ile
md = month_results[50]
mx = month_results[95]
print(f"  月別 想定変動 (1ヶ月分):")
print(f"    最悪 5%パーセンタイル: 回収率 {mn*100:.1f}% (月利益 {(mn-1)*ab/12:+.0f}円)")
print(f"    中央値:              回収率 {md*100:.1f}% (月利益 {(md-1)*ab/12:+.0f}円)")
print(f"    最良 95%パーセンタイル: 回収率 {mx*100:.1f}% (月利益 {(mx-1)*ab/12:+.0f}円)")

print()
print("=" * 130)
print("[推奨] ポートフォリオ B: L4 (3連単) + L2 単勝(1000-2000帯) 併用")
print("=" * 130)
# 両方の bets を結合
all_bets = []
strategies_b = [
    (lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1), bet_trifecta_123),
    (lambda r,i: (1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B),
        bet_win_boat1),
]
for f, b in strategies_b:
    all_bets.extend(collect(f, b))
n_10 = len(all_bets); n_y = int(n_10*ANNUAL_FACTOR)
n_hit = sum(1 for x in all_bets if x>0); n_hit_y = int(n_hit*ANNUAL_FACTOR)
rec_rate = sum(all_bets) / (100*n_10)
ab = n_y * BET_PER_RACE; ar = int(ab*rec_rate); ap = ar - ab
tax, net = tax_calc(ap, n_hit_y*BET_PER_RACE, ar)
print(f"  年レース数:    {n_y:,} (L4 3連単 + L2 単勝 合算)")
print(f"  HIT率:       {n_hit/n_10:.1%}")
print(f"  年回収率:    {rec_rate*100:.1f}%")
print(f"  年投資総額:  {ab:,} 円")
print(f"  年回収総額:  {ar:,} 円")
print(f"  年利益(税前): {ap:+,} 円")
print(f"  税金:       {tax:,} 円")
print(f"  税引後利益:  {net:+,} 円")
print(f"  実効回収率:  {(net+ab)/ab*100:.1f}%")

print()
print("=" * 130)
print("[推奨] ポートフォリオ C: 超厳選 (G1+G2+SG の A1 のみ)")
print("=" * 130)
bets_c = collect(
    lambda r,i: (i["grade"] in (1, 2, 3)
        and 500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B and boat1_class.get(r) == 1),
    bet_trifecta_123)
n_10 = len(bets_c); n_y = int(n_10*ANNUAL_FACTOR)
n_hit = sum(1 for x in bets_c if x>0); n_hit_y = int(n_hit*ANNUAL_FACTOR)
rec_rate = sum(bets_c) / (100*n_10)
ab = n_y * BET_PER_RACE; ar = int(ab*rec_rate); ap = ar - ab
tax, net = tax_calc(ap, n_hit_y*BET_PER_RACE, ar)
print(f"  年レース数:    {n_y:,} (SG/G1/G2 限定。月平均{n_y//12}レース)")
print(f"  HIT率:       {n_hit/n_10:.1%}")
print(f"  年回収率:    {rec_rate*100:.1f}%")
print(f"  年投資総額:  {ab:,} 円")
print(f"  年回収総額:  {ar:,} 円")
print(f"  年利益(税前): {ap:+,} 円")
print(f"  税金:       {tax:,} 円")
print(f"  税引後利益:  {net:+,} 円")
print(f"  実効回収率:  {(net+ab)/ab*100:.1f}%")
