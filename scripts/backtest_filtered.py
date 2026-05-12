"""
過去 10ヶ月 (2025-07-15 ~ 2026-05-12) のバックテスト
「降りる勇気」フィルタの効果検証

各フィルタ層を加えるごとに ROI/回収率がどう変化するか
Bootstrap CI 95% 付き
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
    SELECT r.race_id, r.stadium_number, MIN(pp.payout) as fav_payout
    FROM races r
    JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type = 'trifecta'
    WHERE pp.payout IS NOT NULL
    GROUP BY r.race_id
""")
race_info = {}
for rid, s, mp in cur.fetchall():
    race_info[rid] = {"stadium": s, "fav_payout": mp}
print(f"  fav_payout: {len(race_info):,} レース")

cur = conn.execute("SELECT race_id, boat_number FROM race_results WHERE finishing_position=1")
winner = {rid: bn for rid, bn in cur.fetchall()}
print(f"  winner: {len(winner):,} レース")

cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position FROM race_results
    WHERE finishing_position IN (1, 2, 3)
""")
positions = defaultdict(dict)
for rid, bn, pos in cur.fetchall():
    positions[rid][pos] = bn

cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
pays = defaultdict(dict)
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p
print(f"  payouts loaded")

cur = conn.execute("SELECT race_id, class_number FROM race_entries WHERE boat_number = 1")
boat1_class = dict(cur.fetchall())
print(f"  boat1_class: {len(boat1_class):,} レース\n")

LOSING_VENUES = {2, 7, 10, 21}
QUESTIONABLE = {4, 8, 19, 24}
EXCLUDE_B = LOSING_VENUES | QUESTIONABLE

def bet_win_boat1(rid):
    if winner.get(rid) == 1:
        return pays[rid].get(('win', '1'), 0) or 0
    return 0

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
        print(f"  {name:<58} n=0")
        return
    n_hit = sum(1 for b in bets if b > 0)
    total_bet = 100 * n
    total_pay = sum(bets)
    recovery = total_pay / total_bet * 100
    lo, hi, p0 = bootstrap_ci(bets)
    mark = "[150+]" if recovery >= 150 else "[140+]" if recovery >= 140 else "[130+]" if recovery >= 130 else ""
    profit = total_pay - total_bet
    print(f"  {name:<58} n={n:>6,} HIT{n_hit/n:>5.1%} 回収{recovery:>6.1f}% "
          f"CI[{lo*100+100:>5.1f},{hi*100+100:>5.1f}] P+{p0:>5.1%} 損益{profit:>+10,}円 {mark}")

print("=" * 120)
print("過去10ヶ月バックテスト (2025-07-15 ~ 2026-05-12)")
print("回収 = 投資100に対する回収。150%超えで税引後黒字ライン。CI/P+ = Bootstrap n=1000")
print("=" * 120)

print("\n[Level 0] 基準 (フィルタ無し)")
analyze("L0 全レース -> 1号艇単勝", lambda r,i: True, bet_win_boat1)
analyze("L0 500-1000帯 -> 1号艇単勝",
    lambda r,i: 500 <= i["fav_payout"] < 1000, bet_win_boat1)
analyze("L0 1000-2000帯 -> 1号艇単勝",
    lambda r,i: 1000 <= i["fav_payout"] < 2000, bet_win_boat1)
analyze("L0 500-1000帯 -> 2連単1-2",
    lambda r,i: 500 <= i["fav_payout"] < 1000, bet_exacta_12)
analyze("L0 500-1000帯 -> 3連単1-2-3",
    lambda r,i: 500 <= i["fav_payout"] < 1000, bet_trifecta_123)

print("\n[Level 1] 損切り会場除外 (戸田/蒲郡/三国/芦屋)")
analyze("L1 500-1000帯 単勝 - 損切除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in LOSING_VENUES, bet_win_boat1)
analyze("L1 1000-2000帯 単勝 - 損切除外",
    lambda r,i: 1000 <= i["fav_payout"] < 2000 and i["stadium"] not in LOSING_VENUES, bet_win_boat1)
analyze("L1 500-1000帯 2連単 - 損切除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in LOSING_VENUES, bet_exacta_12)
analyze("L1 500-1000帯 3連単 - 損切除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in LOSING_VENUES, bet_trifecta_123)

print("\n[Level 2] 損切+怪しい会場除外 (+平和島/常滑/下関/大村)")
analyze("L2 500-1000帯 単勝 - B除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1)
analyze("L2 1000-2000帯 単勝 - B除外",
    lambda r,i: 1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1)
analyze("L2 500-1000帯 2連単 - B除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_exacta_12)
analyze("L2 500-1000帯 3連単 - B除外",
    lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_trifecta_123)

print("\n[Level 3] L2 + 1号艇 A1A2 のみ")
analyze("L3 500-1000帯 単勝 - B除外 - A1A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_win_boat1)
analyze("L3 1000-2000帯 単勝 - B除外 - A1A2",
    lambda r,i: (1000 <= i["fav_payout"] < 2000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_win_boat1)
analyze("L3 500-1000帯 2連単 - B除外 - A1A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_exacta_12)
analyze("L3 500-1000帯 3連単 - B除外 - A1A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_trifecta_123)

print("\n[Level 4] L2 + A1のみ (超厳選)")
analyze("L4 500-1000帯 単勝 - B除外 - A1",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 1), bet_win_boat1)
analyze("L4 1000-2000帯 単勝 - B除外 - A1",
    lambda r,i: (1000 <= i["fav_payout"] < 2000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 1), bet_win_boat1)
analyze("L4 500-1000帯 2連単 - B除外 - A1",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 1), bet_exacta_12)
analyze("L4 500-1000帯 3連単 - B除外 - A1",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 1), bet_trifecta_123)

print("\n[Level 5] A2のみ (オッズ妙味狙い)")
analyze("L5 500-1000帯 単勝 - B除外 - A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 2), bet_win_boat1)
analyze("L5 1000-2000帯 単勝 - B除外 - A2",
    lambda r,i: (1000 <= i["fav_payout"] < 2000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 2), bet_win_boat1)
analyze("L5 500-1000帯 2連単 - B除外 - A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 2), bet_exacta_12)
analyze("L5 500-1000帯 3連単 - B除外 - A2",
    lambda r,i: (500 <= i["fav_payout"] < 1000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) == 2), bet_trifecta_123)

print("\n[Level 6] 中穴 2000-5000帯 (A1A2)")
analyze("L6 2000-5000帯 単勝 - B除外 - A1A2",
    lambda r,i: (2000 <= i["fav_payout"] < 5000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_win_boat1)
analyze("L6 2000-5000帯 2連単 - B除外 - A1A2",
    lambda r,i: (2000 <= i["fav_payout"] < 5000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_exacta_12)
analyze("L6 2000-5000帯 3連単 - B除外 - A1A2",
    lambda r,i: (2000 <= i["fav_payout"] < 5000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_trifecta_123)

print("\n[Level 7] ポートフォリオ 多戦略合成 (L2 損切除外)")
# 全戦略のベットを集める
strategies_for_combo = [
    ("単勝 500-1000帯", lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1),
    ("単勝 1000-2000帯", lambda r,i: 1000 <= i["fav_payout"] < 2000 and i["stadium"] not in EXCLUDE_B, bet_win_boat1),
    ("2連単 500-1000帯 1-2", lambda r,i: 500 <= i["fav_payout"] < 1000 and i["stadium"] not in EXCLUDE_B, bet_exacta_12),
]
all_bets = []
for _, f, b in strategies_for_combo:
    for rid, info in race_info.items():
        if rid not in winner: continue
        if f(rid, info):
            all_bets.append(b(rid))
n = len(all_bets)
n_hit = sum(1 for x in all_bets if x > 0)
total_bet = 100 * n
total_pay = sum(all_bets)
recovery = total_pay / total_bet * 100
lo, hi, p0 = bootstrap_ci(all_bets)
profit = total_pay - total_bet
print(f"  L7 単勝500-1000帯 + 単勝1000-2000帯 + 2連単500-1000帯 (合成)")
print(f"     n={n:,}  HIT{n_hit/n:.1%}  回収{recovery:.1f}%  "
      f"CI[{lo*100+100:.1f},{hi*100+100:.1f}] P+{p0:.1%}  損益{profit:+,}円")

print("\n[Level 8] 最強候補: 1000-2000帯 + B除外 + A1A2 + 2連単")
analyze("L8 1000-2000帯 2連単1-2 - B除外 - A1A2",
    lambda r,i: (1000 <= i["fav_payout"] < 2000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_exacta_12)
analyze("L8 1000-2000帯 3連単1-2-3 - B除外 - A1A2",
    lambda r,i: (1000 <= i["fav_payout"] < 2000
        and i["stadium"] not in EXCLUDE_B
        and boat1_class.get(r) in (1,2)), bet_trifecta_123)

# 帯域別 / クラス別 全パターン
print("\n[詳細] 帯域 × クラス の単勝ROIマトリクス (全会場、B除外)")
print(f"  {'帯域':<14} {'A1':>10} {'A2':>10} {'B1':>10} {'全クラス':>10}")
for lo_p, hi_p in [(0,500),(500,1000),(1000,2000),(2000,5000),(5000,10000),(10000,999999)]:
    label = f"{lo_p}-{hi_p if hi_p<999999 else '∞'}"
    line = f"  {label:<14}"
    for cls in [1, 2, 3, 0]:  # 0 = all
        bets = []
        for rid, info in race_info.items():
            if rid not in winner: continue
            if not (lo_p <= info["fav_payout"] < hi_p): continue
            if info["stadium"] in EXCLUDE_B: continue
            if cls != 0 and boat1_class.get(rid) != cls: continue
            bets.append(bet_win_boat1(rid))
        if not bets:
            line += f" {'-':>9}"
            continue
        rec = sum(bets) / len(bets)
        line += f" {rec:>7.1f}% n={len(bets):>4}"
    print(line)
