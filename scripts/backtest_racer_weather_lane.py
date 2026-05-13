"""選手 × 天候 × レーン の 3次元バックテスト

時間分割 (out-of-sample) で検証:
  TRAIN期 (前半6ヶ月): 各選手×レーン×風速帯の1着率を計算 → 特性抽出
  TEST期 (後半4ヶ月): 特性に基づく機械買いで +EV になるか検証

具体的に探す戦略:
  A. 風速別「強風型選手」グループの単勝回収率
  B. 風速別「微風型選手」が1号艇に乗った時の L4 回収率
  C. レーン×会場 特化型選手 (例: 戸田の3コースで強い)
  D. 「成績ジャンプ」型選手: 国/局成績の乖離大きい選手
"""
from __future__ import annotations

import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

random.seed(42)
N_BOOT = 500

conn = sqlite3.connect("data/boatrace.db")
stadium_name = {n: name for n, name in conn.execute("SELECT stadium_number, name FROM stadiums")}

# 全データロード
print("データロード中...")
race_stadium = dict(conn.execute("SELECT race_id, stadium_number FROM races"))
race_date = dict(conn.execute("SELECT race_id, race_date FROM races"))

positions = defaultdict(dict)
for rid, b, p in conn.execute("SELECT race_id, boat_number, finishing_position FROM race_results WHERE finishing_position IN (1,2,3)"):
    positions[rid][p] = b

pays = defaultdict(dict)
for rid, bt, combo, p in conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts"):
    pays[rid][(bt, combo)] = p

race_weather = {}
for rid, w, wd, ws, wh in conn.execute("""
    SELECT race_id, weather_number, wind_direction_number, wind_speed, wave_height
    FROM race_previews WHERE boat_number = 1
"""):
    if ws is not None:
        race_weather[rid] = (w, wd, ws, wh or 0)

# 各レース×艇番×選手番号
race_boat_racer = defaultdict(dict)  # rid -> boat -> racer
boat_class = defaultdict(dict)
for rid, bn, rcr, cls in conn.execute("SELECT race_id, boat_number, racer_number, class_number FROM race_entries"):
    race_boat_racer[rid][bn] = rcr
    boat_class[rid][bn] = cls

# 国/局1%
boat_natl = defaultdict(dict)
boat_local = defaultdict(dict)
for rid, bn, n, l in conn.execute("SELECT race_id, boat_number, national_top_1_percent, local_top_1_percent FROM race_entries"):
    if n is not None: boat_natl[rid][bn] = n
    if l is not None: boat_local[rid][bn] = l

# 時間分割
sorted_dates = sorted(set(race_date.values()))
split_idx = int(len(sorted_dates) * 0.6)
SPLIT_DATE = sorted_dates[split_idx]
print(f"対象期間: {sorted_dates[0]} 〜 {sorted_dates[-1]}")
print(f"TRAIN: 〜 {SPLIT_DATE} ({split_idx+1} 日)")
print(f"TEST:  {SPLIT_DATE} 以降 ({len(sorted_dates)-split_idx-1} 日)")


def in_train(rid):
    return race_date.get(rid, "") < SPLIT_DATE

def in_test(rid):
    return race_date.get(rid, "") >= SPLIT_DATE


def wind_bin(ws):
    if ws <= 2: return "calm"
    if ws <= 4: return "light"
    if ws <= 6: return "moderate"
    if ws <= 8: return "strong"
    return "gale"


def is_strong_wind(rid):
    return rid in race_weather and race_weather[rid][2] >= 5


def bootstrap_ci(payouts, n_bet=1):
    n = len(payouts)
    if n == 0: return None, None, None
    rois = []
    for _ in range(N_BOOT):
        s = random.choices(payouts, k=n)
        rois.append(sum(s) / (100 * n_bet * n) * 100)
    rois.sort()
    return rois[int(N_BOOT * 0.025)], rois[int(N_BOOT * 0.975)], sum(1 for r in rois if r > 100) / N_BOOT


def calc_payouts(rids_payouts, n_bet=1):
    n = len(rids_payouts)
    if n == 0: return None
    payouts = [p for _, p in rids_payouts]
    hit = sum(1 for p in payouts if p > 0)
    total = sum(payouts)
    cost = 100 * n_bet * n
    rec = total / max(1, cost) * 100
    profit = total - cost
    lo, hi, p_pos = bootstrap_ci(payouts, n_bet)
    return {"n": n, "hit": hit, "rec": rec, "profit": profit, "ci_lo": lo, "ci_hi": hi, "p_pos": p_pos}


def pay_win(rid, b):
    return pays.get(rid, {}).get(("win", str(b)), 0) or 0 if positions[rid].get(1) == b else 0
def pay_exa(rid, a, b):
    return pays.get(rid, {}).get(("exacta", f"{a}-{b}"), 0) or 0 if positions[rid].get(1) == a and positions[rid].get(2) == b else 0


# ============================================================
# STEP 1: TRAIN期 各選手×レーン×風速 の1着率集計
# ============================================================
print("\n[STEP 1] TRAIN期で 選手×レーン×風速 の1着率テーブル構築")

# racer_lane_wind = {(racer, lane, wind_bin): [n, wins]}
racer_lane_wind = defaultdict(lambda: [0, 0])
racer_lane_calm = defaultdict(lambda: [0, 0])    # 微風時
racer_lane_strong = defaultdict(lambda: [0, 0])  # 強風時

for rid in race_stadium:
    if not in_train(rid): continue
    if rid not in race_weather: continue
    if 1 not in positions.get(rid, {}): continue
    ws = race_weather[rid][2]
    winner = positions[rid][1]
    for lane, racer in race_boat_racer.get(rid, {}).items():
        racer_lane_wind[(racer, lane, wind_bin(ws))][0] += 1
        if winner == lane:
            racer_lane_wind[(racer, lane, wind_bin(ws))][1] += 1
        # 微風 vs 強風 (二値)
        if ws <= 4:
            racer_lane_calm[(racer, lane)][0] += 1
            if winner == lane: racer_lane_calm[(racer, lane)][1] += 1
        elif ws >= 5:
            racer_lane_strong[(racer, lane)][0] += 1
            if winner == lane: racer_lane_strong[(racer, lane)][1] += 1


# ============================================================
# STEP 2: 「強風型」「微風型」選手の特定
# ============================================================
print("[STEP 2] 強風時 vs 微風時で勝率乖離が大きい選手を抽出")

# 各 (racer, lane) で strong率 - calm率
strong_specialists = defaultdict(set)   # lane -> set(racer)
calm_specialists = defaultdict(set)     # lane -> set(racer)

for (racer, lane), (n_strong, w_strong) in racer_lane_strong.items():
    (n_calm, w_calm) = racer_lane_calm.get((racer, lane), (0, 0))
    # 両方サンプル 5 以上を要求 (個人で十分な集計)
    if n_strong < 5 or n_calm < 5: continue
    rate_strong = w_strong / n_strong
    rate_calm = w_calm / n_calm
    diff = rate_strong - rate_calm
    # 強風時に +10pt 以上勝率上昇
    if diff >= 0.10:
        strong_specialists[lane].add(racer)
    # 微風時に +10pt 以上勝率上昇
    elif diff <= -0.10:
        calm_specialists[lane].add(racer)

print(f"  各レーンの『強風型』選手数:")
for lane in range(1, 7):
    print(f"    {lane}号艇: {len(strong_specialists[lane])} 人")
print(f"  各レーンの『微風型』選手数:")
for lane in range(1, 7):
    print(f"    {lane}号艇: {len(calm_specialists[lane])} 人")


# ============================================================
# STEP 3: TEST期で「強風型選手 × 強風 × 該当レーン」 機械買い
# ============================================================
print("\n" + "=" * 100)
print("【A】 TEST期 (out-of-sample): 強風型選手が強風で N号艇に乗った時の単勝買い")
print("=" * 100)
print(f"\n  {'レーン':<6} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")
for lane in range(1, 7):
    rids_payouts = []
    for rid in race_stadium:
        if not in_test(rid): continue
        if not is_strong_wind(rid): continue
        if 1 not in positions.get(rid, {}): continue
        racer = race_boat_racer.get(rid, {}).get(lane)
        if racer not in strong_specialists.get(lane, set()): continue
        rids_payouts.append((rid, pay_win(rid, lane)))
    r = calc_payouts(rids_payouts)
    if r and r["n"] >= 5:
        mark = " ✅+EV" if r["rec"] >= 100 else ""
        ci_str = f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]"
        print(f"  {lane}号艇 {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {ci_str:>20} {r['profit']:>+8,}円{mark}")


# ============================================================
# 【B】 微風型選手 × 微風 × 1号艇 (L4 強化版)
# ============================================================
print("\n" + "=" * 100)
print("【B】 TEST期: 微風型1号艇選手 × 微風 × L4 風買い (本命500-1000円, 3連単1-2-3)")
print("=" * 100)

# 本命オッズ
race_fav = {}
for rid in race_stadium:
    if not in_test(rid): continue
    tri_pays = [v for (bt, _), v in pays.get(rid, {}).items() if bt == "trifecta" and v]
    if tri_pays:
        race_fav[rid] = min(tri_pays)

def pay_tri_123(rid):
    if positions[rid].get(1) == 1 and positions[rid].get(2) == 2 and positions[rid].get(3) == 3:
        return pays.get(rid, {}).get(("trifecta", "1-2-3"), 0) or 0
    return 0

EXCLUDE_B = {2, 4, 7, 8, 10, 19, 21, 24}

cases = [
    ("通常 L4 (TEST期)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1)),
    ("L4 × 微風型1号艇選手",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and race_boat_racer.get(rid, {}).get(1) in calm_specialists.get(1, set())
                  and rid in race_weather and race_weather[rid][2] <= 4)),
    ("L4 × 強風型1号艇選手 × 強風",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and race_boat_racer.get(rid, {}).get(1) in strong_specialists.get(1, set())
                  and rid in race_weather and race_weather[rid][2] >= 5)),
    ("L4 - 微風型を除外 (微風型は1号艇不利)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and race_boat_racer.get(rid, {}).get(1) not in calm_specialists.get(1, set()))),
]

print(f"\n  {'シナリオ':<32} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")
for label, fil in cases:
    rids_payouts = []
    for rid in race_stadium:
        if not in_test(rid): continue
        if not fil(rid): continue
        rids_payouts.append((rid, pay_tri_123(rid)))
    r = calc_payouts(rids_payouts)
    if r and r["n"] >= 5:
        mark = " ✅+EV" if r["rec"] >= 100 else ""
        ci_str = f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]"
        print(f"  {label:<32} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {ci_str:>20} {r['profit']:>+8,}円{mark}")


# ============================================================
# 【C】 局成績ジャンプ型: 国1%と局1%の差が大きい選手 (会場相性)
# ============================================================
print("\n" + "=" * 100)
print("【C】 TEST期: 局成績ジャンプ型 (国1% < 局1% +1pt 以上の選手が1号艇)")
print("=" * 100)
print("  仮説: その会場で過去に好成績 = 出走中の会場で特に強い → 1号艇本命がより信頼")

cases_c = [
    ("通常 L4 (再掲)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1)),
    ("L4 × 国<局+1.0pt以上",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and rid in boat_local and rid in boat_natl
                  and 1 in boat_local[rid] and 1 in boat_natl[rid]
                  and boat_local[rid][1] - boat_natl[rid][1] >= 1.0)),
    ("L4 × 局>=7.0% (会場で抜群)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and rid in boat_local and 1 in boat_local[rid]
                  and boat_local[rid][1] >= 7.0)),
    ("L4 × 国>=7.0% (全国級)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and rid in boat_natl and 1 in boat_natl[rid]
                  and boat_natl[rid][1] >= 7.0)),
    ("L4 × 国>=7.0% × 局>=7.0% (全国級+地元級)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1
                  and rid in boat_natl and 1 in boat_natl[rid] and boat_natl[rid][1] >= 7.0
                  and rid in boat_local and 1 in boat_local[rid] and boat_local[rid][1] >= 7.0)),
]

print(f"\n  {'シナリオ':<36} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")
for label, fil in cases_c:
    rids_payouts = []
    for rid in race_stadium:
        if not in_test(rid): continue
        if not fil(rid): continue
        rids_payouts.append((rid, pay_tri_123(rid)))
    r = calc_payouts(rids_payouts)
    if r and r["n"] >= 10:
        mark = " ✅+EV" if r["rec"] >= 100 else ""
        ci_str = f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]"
        print(f"  {label:<36} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {ci_str:>20} {r['profit']:>+8,}円{mark}")


# ============================================================
# 【D】 強風型2-3-4号艇選手 × その選手のレーン×強風 = ヒモ抜けレース?
# ============================================================
print("\n" + "=" * 100)
print("【D】 TEST期: 強風型 N号艇 (N=3,4,5) 選手 × 強風 × 2連単 N-1 (本命を差す)")
print("=" * 100)
print(f"\n  {'戦略':<35} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")
for N in [2, 3, 4, 5]:
    for bet_pat in ["win", "exa_to_1"]:
        rids_payouts = []
        for rid in race_stadium:
            if not in_test(rid): continue
            if not is_strong_wind(rid): continue
            racer = race_boat_racer.get(rid, {}).get(N)
            if racer not in strong_specialists.get(N, set()): continue
            if bet_pat == "win":
                rids_payouts.append((rid, pay_win(rid, N)))
                label = f"強風型{N}号艇 × 強風 × 単勝{N}"
            else:
                rids_payouts.append((rid, pay_exa(rid, N, 1)))
                label = f"強風型{N}号艇 × 強風 × 2連単{N}-1"
        r = calc_payouts(rids_payouts)
        if r and r["n"] >= 10:
            mark = " ✅+EV" if r["rec"] >= 100 else ""
            ci_str = f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]"
            print(f"  {label:<35} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
                  f"{r['rec']:>6.1f}% {ci_str:>20} {r['profit']:>+8,}円{mark}")


# ============================================================
# 【E】 ベンチマーク: TEST期の全体水準と比較
# ============================================================
print("\n" + "=" * 100)
print("【E】 ベンチマーク (TEST期の機械買い)")
print("=" * 100)
benchmarks = [
    ("全レース 単勝1", lambda rid: True, lambda rid: pay_win(rid, 1)),
    ("全レース 3連単1-2-3", lambda rid: True, lambda rid: pay_tri_123(rid)),
    ("TEST期 L4 (基本)",
     lambda rid: (rid in race_fav and 500 <= race_fav[rid] < 1000
                  and race_stadium.get(rid) not in EXCLUDE_B
                  and boat_class.get(rid, {}).get(1) == 1),
     lambda rid: pay_tri_123(rid)),
]
print(f"\n  {'ベンチ':<20} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")
for label, fil, pay_fn in benchmarks:
    rids_payouts = []
    for rid in race_stadium:
        if not in_test(rid): continue
        if not fil(rid): continue
        rids_payouts.append((rid, pay_fn(rid)))
    r = calc_payouts(rids_payouts)
    if r:
        mark = " ✅" if r["rec"] >= 100 else ""
        ci_str = f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]"
        print(f"  {label:<20} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {ci_str:>20} {r['profit']:>+8,}円{mark}")

conn.close()
