"""蒲郡 × 強風 × 3号艇 戦略の深掘りバックテスト

前回検証で見つかった +EV 候補:
  蒲郡(s=7) × 風速5+ × 3号艇単勝 → 回収率 150.9% (n=772)

この戦略を多角的に絞り込み、本格運用可能か確認:
  ① 風速閾値の最適化 (5+, 6+, 7+, 8+)
  ② 3号艇のクラス別 (A1/A2/B1/B2)
  ③ 買い目別 (単勝/2連単/3連単頭固定)
  ④ 月別・年別の安定性 (一時的な歪みではないか)
  ⑤ 似た特性の会場 (アウト勢有利) で同条件が +EV か → 横展開可能性
  ⑥ Bootstrap CI の信頼度
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
N_BOOT = 1000

conn = sqlite3.connect("data/boatrace.db")
stadium_name = {n: name for n, name in conn.execute("SELECT stadium_number, name FROM stadiums")}

# 基本データ
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

boat_class = defaultdict(dict)
for rid, bn, cls in conn.execute("SELECT race_id, boat_number, class_number FROM race_entries"):
    boat_class[rid][bn] = cls


def bootstrap_ci(payouts, n_bet=1):
    n = len(payouts)
    if n == 0: return None, None, None, None
    rois = []
    for _ in range(N_BOOT):
        s = random.choices(payouts, k=n)
        rois.append(sum(s) / (100 * n_bet * n) * 100)
    rois.sort()
    return (rois[int(N_BOOT * 0.025)], rois[int(N_BOOT * 0.975)],
            rois[int(N_BOOT * 0.5)], sum(1 for r in rois if r > 100) / N_BOOT)


def calc(filter_fn, payout_fn, n_bet=1):
    payouts = []
    rids = []
    for rid in race_stadium:
        if not filter_fn(rid): continue
        payouts.append(payout_fn(rid))
        rids.append(rid)
    n = len(payouts)
    if n == 0: return None
    hit = sum(1 for p in payouts if p > 0)
    total = sum(payouts)
    cost = 100 * n_bet * n
    rec = total / max(1, cost) * 100
    profit = total - cost
    lo, hi, med, p_pos = bootstrap_ci(payouts, n_bet)
    return {"n": n, "hit": hit, "rec": rec, "profit": profit, "ci_lo": lo, "ci_hi": hi,
            "ci_med": med, "p_pos": p_pos, "rids": rids, "payouts": payouts}


def pay_win(rid, b):
    return pays.get(rid, {}).get(("win", str(b)), 0) or 0 if positions[rid].get(1) == b else 0

def pay_exa(rid, a, b):
    return pays.get(rid, {}).get(("exacta", f"{a}-{b}"), 0) or 0 if positions[rid].get(1) == a and positions[rid].get(2) == b else 0

def pay_tri(rid, a, b, c):
    return pays.get(rid, {}).get(("trifecta", f"{a}-{b}-{c}"), 0) or 0 if positions[rid].get(1) == a and positions[rid].get(2) == b and positions[rid].get(3) == c else 0


KAMAGORI = 7  # 蒲郡

print("=" * 100)
print("蒲郡 × 強風 × 3号艇 戦略 詳細バックテスト")
print("=" * 100)


# ============================================================
# ① 風速閾値の最適化
# ============================================================
print("\n【①】 風速閾値ごとの 蒲郡×3号艇単勝 回収率")
print(f"  {'閾値':<10} {'n':>5} {'HIT%':>6} {'回収':>7} {'中央値':>7} {'CI 95%':>20} {'P+':>6}")
for thr in [0, 3, 4, 5, 6, 7, 8]:
    f = lambda rid, t=thr: (race_stadium.get(rid) == KAMAGORI
                            and rid in race_weather
                            and race_weather[rid][2] >= t)
    r = calc(f, lambda rid: pay_win(rid, 3))
    if r:
        mark = " ✅+EV" if r["rec"] >= 100 else ""
        print(f"  風速≥{thr:<5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {r['ci_med']:>6.1f}% "
              f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['p_pos']*100:>5.1f}%{mark}")


# ============================================================
# ② 3号艇クラス別
# ============================================================
print("\n【②】 蒲郡×風速5+×3号艇 クラス別")
class_names = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
print(f"  {'3号艇':<6} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20}")
for cls in [1, 2, 3, 4]:
    f = lambda rid, c=cls: (race_stadium.get(rid) == KAMAGORI
                            and rid in race_weather and race_weather[rid][2] >= 5
                            and boat_class.get(rid, {}).get(3) == c)
    r = calc(f, lambda rid: pay_win(rid, 3))
    if r and r["n"] >= 20:
        mark = " ✅" if r["rec"] >= 100 else ""
        print(f"  {class_names[cls]:<6} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]{mark}")


# ============================================================
# ③ 買い目別
# ============================================================
print("\n【③】 蒲郡×風速5+ 各種買い目")
f_kg5 = lambda rid: (race_stadium.get(rid) == KAMAGORI
                     and rid in race_weather and race_weather[rid][2] >= 5)

print(f"  {'買い目':<22} {'pt/R':>5} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>10}")

# 単勝3
r = calc(f_kg5, lambda rid: pay_win(rid, 3), 1)
print(f"  単勝 3                  {1:>5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+9,}円")

# 2連単 3-* (5点)
def pay_exa_box(rid, head):
    if positions[rid].get(1) != head: return 0
    b2 = positions[rid].get(2)
    return pays.get(rid, {}).get(("exacta", f"{head}-{b2}"), 0) or 0 if b2 else 0

r = calc(f_kg5, lambda rid: pay_exa_box(rid, 3), 5)
print(f"  2連単 3-* (5点)          {5:>5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+9,}円")

# 2連単 3-1 (1点)
r = calc(f_kg5, lambda rid: pay_exa(rid, 3, 1), 1)
print(f"  2連単 3-1                {1:>5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+9,}円")

# 2連単 3-2 (1点)
r = calc(f_kg5, lambda rid: pay_exa(rid, 3, 2), 1)
print(f"  2連単 3-2                {1:>5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+9,}円")

# 3連単 3-*-* (20点)
def pay_tri_head(rid, head):
    if positions[rid].get(1) != head: return 0
    b2, b3 = positions[rid].get(2), positions[rid].get(3)
    if b2 and b3 and b2 != head and b3 != head:
        return pays.get(rid, {}).get(("trifecta", f"{head}-{b2}-{b3}"), 0) or 0
    return 0

r = calc(f_kg5, lambda rid: pay_tri_head(rid, 3), 20)
print(f"  3連単 3-*-* (20点)       {20:>5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+9,}円")


# ============================================================
# ④ 月別の安定性
# ============================================================
print("\n【④】 月別 蒲郡×風速5+×3号艇単勝 (時系列安定性)")
month_data = defaultdict(list)
for rid in race_stadium:
    if not (race_stadium.get(rid) == KAMAGORI
            and rid in race_weather and race_weather[rid][2] >= 5): continue
    d = race_date.get(rid, "")
    if len(d) >= 7:
        ym = d[:7]
        p = pay_win(rid, 3)
        month_data[ym].append(p)

print(f"  {'月':<10} {'n':>4} {'HIT':>5} {'回収':>7} {'損益':>9}")
total_n, total_hit, total_pay = 0, 0, 0
for ym in sorted(month_data):
    ps = month_data[ym]
    n = len(ps)
    h = sum(1 for p in ps if p > 0)
    rec = sum(ps) / max(1, 100 * n) * 100
    profit = sum(ps) - 100 * n
    total_n += n; total_hit += h; total_pay += sum(ps)
    mark = "✅" if rec >= 100 else " "
    print(f"  {ym:<10} {n:>4} {h/n*100:>4.1f}% {rec:>6.1f}% {profit:>+8,}円 {mark}")
print(f"  {'通算':<10} {total_n:>4} {total_hit/total_n*100:>4.1f}% "
      f"{total_pay/max(1,100*total_n)*100:>6.1f}% {total_pay - 100*total_n:>+8,}円")

# 月別黒字率
pos_months = sum(1 for ym, ps in month_data.items() if sum(ps) > 100*len(ps))
total_months = len(month_data)
print(f"\n  → 黒字月: {pos_months}/{total_months} ({pos_months/max(1,total_months)*100:.0f}%)")


# ============================================================
# ⑤ 横展開: 似た会場 (アウト勢有利) で同戦略
# ============================================================
print("\n【⑤】 横展開: 各会場 × 風速5+ × 3号艇単勝 (n>=200, 全会場ランキング)")
print(f"  {'順位':>3} {'会場':<8} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>9}")

rank = []
for s in range(1, 25):
    f = lambda rid, ss=s: (race_stadium.get(rid) == ss
                            and rid in race_weather and race_weather[rid][2] >= 5)
    r = calc(f, lambda rid: pay_win(rid, 3))
    if r and r["n"] >= 200:
        rank.append((s, r))
rank.sort(key=lambda x: -x[1]["rec"])
for i, (s, r) in enumerate(rank, 1):
    mark = " ✅" if r["rec"] >= 100 else ""
    print(f"  {i:>3} {s:>2} {stadium_name[s]:<5} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
          f"{r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}] {r['profit']:>+8,}円{mark}")


# ============================================================
# ⑥ 全コース×全会場×風速5+ で機械買い +EV 抽出
# ============================================================
print("\n【⑥】 全 (会場×N号艇) × 風速5+ 単勝で +EV のもの (n>=100, 回収>=100%)")
print(f"  {'会場':<8} {'コース':<3} {'n':>5} {'HIT%':>5} {'回収':>7} {'CI 95%':>20}")

ev_list = []
for s in range(1, 25):
    for N in [2, 3, 4, 5, 6]:
        f = lambda rid, ss=s: (race_stadium.get(rid) == ss
                                and rid in race_weather and race_weather[rid][2] >= 5)
        r = calc(f, lambda rid, n=N: pay_win(rid, n))
        if r and r["n"] >= 100 and r["rec"] >= 100:
            ev_list.append((s, N, r))
ev_list.sort(key=lambda x: -x[2]["rec"])
for s, N, r in ev_list:
    mark = " ✅" if r["ci_lo"] >= 100 else " ⚠️CI下限<100"
    print(f"  {s:>2} {stadium_name[s]:<5} {N}号艇 {r['n']:>5,} {r['hit']/r['n']*100:>4.1f}% "
          f"{r['rec']:>6.1f}% [{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]{mark}")


conn.close()
