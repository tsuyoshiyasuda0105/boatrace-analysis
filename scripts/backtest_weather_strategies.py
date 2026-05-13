"""気象条件 (風速・風向・波高・天候) を使った戦略の本格バックテスト

検証する戦略 (実データ全件で機械買い):
  A. 風速別 1号艇単勝 / 1-2 / 1-2-3
  B. 風速別 N号艇単勝 (N=2..6)
  C. 風速別 L4 戦略 (1号艇A1 + 本命500-1000円 + 3連単1-2-3)
  D. 風速×会場 で +EV 領域抽出
  E. 波高別 1コース回収率
  F. 天候 (weather_number) 別 戦略
  G. 風向×会場 (各会場で「追い風になる風向」を統計的に同定)
  H. 強風 (5+) でアウト勢狙い (3コース・4コース・5コース) 単勝 / 2連単 / 3連単
  I. 風速 × オッズ帯 × クラス の多次元絞り込み

全部 Bootstrap CI 付き。
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

# レース基本情報
race_stadium = dict(conn.execute("SELECT race_id, stadium_number FROM races"))
print(f"全レース: {len(race_stadium):,}")

# 順位
positions = defaultdict(dict)
for rid, b, p in conn.execute("SELECT race_id, boat_number, finishing_position FROM race_results WHERE finishing_position IN (1,2,3)"):
    positions[rid][p] = b
print(f"順位データ:  {sum(1 for r in race_stadium if 1 in positions[r]):,}")

# 払戻
pays = defaultdict(dict)
for rid, bt, combo, p in conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts"):
    pays[rid][(bt, combo)] = p

# 気象 (race_id -> (weather, wind_dir, wind_speed, wave_height))
race_weather = {}
for rid, w, wd, ws, wh in conn.execute("""
    SELECT race_id, weather_number, wind_direction_number, wind_speed, wave_height
    FROM race_previews WHERE boat_number = 1
"""):
    if ws is not None:
        race_weather[rid] = (w, wd, ws, wh or 0)
print(f"気象データ:  {len(race_weather):,}")

# 1艇クラス
boat_class = defaultdict(dict)
for rid, bn, cls in conn.execute("SELECT race_id, boat_number, class_number FROM race_entries"):
    boat_class[rid][bn] = cls


def bootstrap_ci(payouts, n_bet=1):
    n = len(payouts)
    if n == 0: return None, None, None
    cost = 100 * n_bet
    rois = []
    for _ in range(N_BOOT):
        s = random.choices(payouts, k=n)
        rois.append(sum(s) / (cost * n) * 100)
    rois.sort()
    return rois[int(N_BOOT * 0.025)], rois[int(N_BOOT * 0.975)], sum(1 for r in rois if r > 100) / N_BOOT


def calc(filter_fn, payout_fn, n_bet=1):
    payouts = []
    for rid in race_stadium:
        if not filter_fn(rid): continue
        payouts.append(payout_fn(rid))
    n = len(payouts)
    if n == 0: return None
    hit = sum(1 for p in payouts if p > 0)
    total = sum(payouts)
    cost = 100 * n_bet * n
    rec = total / max(1, cost) * 100
    profit = total - cost
    lo, hi, p_pos = bootstrap_ci(payouts, n_bet)
    return {"n": n, "hit": hit, "rec": rec, "profit": profit, "ci_lo": lo, "ci_hi": hi, "p_pos": p_pos}


def fmt(r):
    if r is None or r["n"] == 0: return "  (n=0)"
    return (f"n={r['n']:>6,} HIT={r['hit']/r['n']*100:>5.1f}% "
            f"回収={r['rec']:>6.1f}% CI=[{r['ci_lo']:>6.1f},{r['ci_hi']:>6.1f}] "
            f"P+={r['p_pos']*100:>5.1f}% 損益={r['profit']:>+10,}円")


# 風速 bin
def wind_bin(ws):
    if ws <= 2: return "0-2"
    if ws <= 4: return "3-4"
    if ws <= 6: return "5-6"
    if ws <= 8: return "7-8"
    return "9+"


# 支払い関数群
def pay_win(rid, b):
    return pays.get(rid, {}).get(("win", str(b)), 0) or 0 if positions[rid].get(1) == b else 0
def pay_exa(rid, a, b):
    return pays.get(rid, {}).get(("exacta", f"{a}-{b}"), 0) or 0 if positions[rid].get(1) == a and positions[rid].get(2) == b else 0
def pay_tri(rid, a, b, c):
    return pays.get(rid, {}).get(("trifecta", f"{a}-{b}-{c}"), 0) or 0 if positions[rid].get(1) == a and positions[rid].get(2) == b and positions[rid].get(3) == c else 0


# ============================================================
# A. 風速別 1号艇 単勝/2連単1-2/3連単1-2-3
# ============================================================
print("\n" + "=" * 100)
print("【A】 風速別 1号艇 各買い目 機械買い")
print("=" * 100)
for bet_name, pay_fn in [("単勝1", lambda r: pay_win(r, 1)),
                          ("2連単1-2", lambda r: pay_exa(r, 1, 2)),
                          ("3連単1-2-3", lambda r: pay_tri(r, 1, 2, 3))]:
    print(f"\n  ◆ {bet_name}")
    for bin_label in ["0-2", "3-4", "5-6", "7-8", "9+"]:
        f = lambda rid, bl=bin_label: rid in race_weather and wind_bin(race_weather[rid][2]) == bl
        r = calc(f, pay_fn)
        if r and r["n"]:
            print(f"    風速 {bin_label:<4} : {fmt(r)}")


# ============================================================
# B. 風速別 N号艇 単勝 (N=2..6)
# ============================================================
print("\n" + "=" * 100)
print("【B】 風速別 N号艇 単勝 (アウト勢期待値)")
print("=" * 100)
for N in [2, 3, 4, 5, 6]:
    print(f"\n  ◆ {N}号艇 単勝")
    for bin_label in ["0-2", "3-4", "5-6", "7-8", "9+"]:
        f = lambda rid, bl=bin_label: rid in race_weather and wind_bin(race_weather[rid][2]) == bl
        r = calc(f, lambda rid, n=N: pay_win(rid, n))
        if r and r["n"]:
            mark = " ✅+EV" if r["rec"] >= 100 else ""
            print(f"    風速 {bin_label:<4} : {fmt(r)}{mark}")


# ============================================================
# C. 風速別 L4 戦略
# ============================================================
print("\n" + "=" * 100)
print("【C】 風速別 L4 戦略 (1号艇A1 + 本命500-1000円 + B除外 + 3連単1-2-3)")
print("=" * 100)

# L4 条件のための本命オッズと B除外
EXCLUDE_B = {2, 4, 7, 8, 10, 19, 21, 24}
race_fav = {}  # 本命オッズ (1-2-3 ではなく最小3連単オッズ)
for rid in race_stadium:
    tri_pays = [v for (bt, _), v in pays.get(rid, {}).items() if bt == "trifecta" and v]
    if tri_pays:
        race_fav[rid] = min(tri_pays)


def is_l4(rid):
    if rid not in race_fav: return False
    if not (500 <= race_fav[rid] < 1000): return False
    if race_stadium.get(rid) in EXCLUDE_B: return False
    if boat_class.get(rid, {}).get(1) != 1: return False
    return True


for bin_label in ["0-2", "3-4", "5-6", "7-8", "9+"]:
    f = lambda rid, bl=bin_label: (rid in race_weather and wind_bin(race_weather[rid][2]) == bl and is_l4(rid))
    r = calc(f, lambda rid: pay_tri(rid, 1, 2, 3))
    print(f"  風速 {bin_label:<4} : {fmt(r) if r else '(n=0)'}")


# ============================================================
# D. 風速×会場 +EV 領域抽出
# ============================================================
print("\n" + "=" * 100)
print("【D】 風速 × 会場 で 3コース単勝 +EV 検証 (強風時イン崩れの会場特定)")
print("=" * 100)
print(f"\n  ◆ 風速 5+ で N号艇単勝 各会場別 (n>=20 のみ)")
print(f"  {'会場':<7} {'コース':<6} {'n':>4} {'HIT%':>5} {'回収':>7} {'損益':>9}")

ev_candidates = []
for s in range(1, 25):
    for N in [3, 4, 5]:
        f = lambda rid, ss=s, n=N: (rid in race_weather and race_weather[rid][2] >= 5
                                     and race_stadium.get(rid) == ss)
        r = calc(f, lambda rid, n=N: pay_win(rid, n))
        if r and r["n"] >= 20 and r["rec"] >= 80:
            ev_candidates.append((s, N, r))

ev_candidates.sort(key=lambda x: -x[2]["rec"])
for s, N, r in ev_candidates[:20]:
    mark = " ✅" if r["rec"] >= 100 else ""
    print(f"  {s:>2} {stadium_name[s]:<5} {N}号艇   {r['n']:>4} {r['hit']/r['n']*100:>4.1f}% "
          f"{r['rec']:>6.1f}% {r['profit']:>+8,}円{mark}")


# ============================================================
# E. 波高別 1コース回収率
# ============================================================
print("\n" + "=" * 100)
print("【E】 波高別 1号艇/3-5号艇 戦略")
print("=" * 100)

def wave_bin(wh):
    if wh <= 1: return "0-1"
    if wh <= 3: return "2-3"
    if wh <= 5: return "4-5"
    return "6+"

for bet_name, pay_fn in [("単勝1", lambda r: pay_win(r, 1)),
                          ("3連単1-2-3", lambda r: pay_tri(r, 1, 2, 3)),
                          ("単勝3 (中アウト)", lambda r: pay_win(r, 3)),
                          ("単勝4 (カド)", lambda r: pay_win(r, 4))]:
    print(f"\n  ◆ {bet_name}")
    for bin_label in ["0-1", "2-3", "4-5", "6+"]:
        f = lambda rid, bl=bin_label: rid in race_weather and wave_bin(race_weather[rid][3]) == bl
        r = calc(f, pay_fn)
        if r and r["n"] >= 20:
            mark = " ✅" if r["rec"] >= 100 else ""
            print(f"    波高 {bin_label:<4} : {fmt(r)}{mark}")


# ============================================================
# F. 天候別
# ============================================================
print("\n" + "=" * 100)
print("【F】 天候別 1号艇単勝 / 3-5号艇単勝 (weather_number)")
print("=" * 100)

weather_names = {1: "晴", 2: "曇", 3: "雨", 4: "雪", 5: "霧"}

for bet_name, pay_fn in [("単勝1", lambda r: pay_win(r, 1)),
                          ("3連単1-2-3", lambda r: pay_tri(r, 1, 2, 3)),
                          ("単勝3", lambda r: pay_win(r, 3))]:
    print(f"\n  ◆ {bet_name}")
    for w in [1, 2, 3, 4]:
        f = lambda rid, ww=w: rid in race_weather and race_weather[rid][0] == ww
        r = calc(f, pay_fn)
        if r and r["n"] >= 50:
            wname = weather_names.get(w, f"#{w}")
            mark = " ✅" if r["rec"] >= 100 else ""
            print(f"    天候 {wname:<3} : {fmt(r)}{mark}")


# ============================================================
# G. 強風 5+ のアウト勢 (3-5コース) 各買い目で +EV 探索
# ============================================================
print("\n" + "=" * 100)
print("【G】 強風 (風速5+) アウト勢 機械買い 多角検証")
print("=" * 100)

f_strong = lambda rid: rid in race_weather and race_weather[rid][2] >= 5

bet_patterns = [
    ("単勝3", lambda r: pay_win(r, 3)),
    ("単勝4", lambda r: pay_win(r, 4)),
    ("単勝5", lambda r: pay_win(r, 5)),
    ("2連単 3-* BOX 5pt",   lambda r: pay_3star_box(r, 3), 5),
    ("2連単 4-* BOX 5pt",   lambda r: pay_3star_box(r, 4), 5),
    ("3連単 3-*-* BOX 20pt", lambda r: pay_tri_head(r, 3), 20),
    ("3連単 4-*-* BOX 20pt", lambda r: pay_tri_head(r, 4), 20),
]


def pay_3star_box(rid, head):
    """N-* 2連単で 1着がheadのとき2着の払戻"""
    if positions[rid].get(1) != head: return 0
    b2 = positions[rid].get(2)
    if b2:
        return pays.get(rid, {}).get(("exacta", f"{head}-{b2}"), 0) or 0
    return 0


def pay_tri_head(rid, head):
    """N-*-* 3連単で1着がheadのとき"""
    if positions[rid].get(1) != head: return 0
    b2 = positions[rid].get(2); b3 = positions[rid].get(3)
    if b2 and b3 and b2 != head and b3 != head:
        return pays.get(rid, {}).get(("trifecta", f"{head}-{b2}-{b3}"), 0) or 0
    return 0


print(f"\n  対象: 風速 5 以上のレース全て")
for name, pay_fn, *n_bet_list in bet_patterns:
    n_bet = n_bet_list[0] if n_bet_list else 1
    r = calc(f_strong, pay_fn, n_bet)
    mark = " ✅+EV" if r and r["rec"] >= 100 else ""
    print(f"  {name:<24} : {fmt(r)}{mark}")


# ============================================================
# H. 風速 × 1号艇クラス
# ============================================================
print("\n" + "=" * 100)
print("【H】 風速 × 1号艇クラス で L4 風買い (3連単1-2-3)")
print("=" * 100)
print(f"\n  {'1号艇クラス':<12} {'風速':<8} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20}")

class_names = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
for cls in [1, 2, 3]:
    for bin_label in ["0-2", "3-4", "5-6", "7-8", "9+"]:
        f = lambda rid, cc=cls, bl=bin_label: (
            rid in race_weather and wind_bin(race_weather[rid][2]) == bl
            and boat_class.get(rid, {}).get(1) == cc
            and rid in race_fav and 500 <= race_fav[rid] < 1000
        )
        r = calc(f, lambda rid: pay_tri(rid, 1, 2, 3))
        if r and r["n"] >= 30:
            mark = " ✅" if r["rec"] >= 100 else ""
            print(f"  {class_names.get(cls, '?'):<12} 風速{bin_label:<5} {r['n']:>4,} "
                  f"{r['hit']/r['n']*100:>5.1f}% {r['rec']:>6.1f}% "
                  f"[{r['ci_lo']:>5.1f},{r['ci_hi']:>5.1f}]{mark}")


# ============================================================
# I. 結論: 最も期待値高い「気象+L4」組み合わせを抽出
# ============================================================
print("\n" + "=" * 100)
print("【I】 +EV 領域の総合ランキング (n>=30, 回収率>=130%)")
print("=" * 100)

candidates = []

# L4 × 風速
for bin_label in ["0-2", "3-4", "5-6", "7-8", "9+"]:
    f = lambda rid, bl=bin_label: (rid in race_weather and wind_bin(race_weather[rid][2]) == bl and is_l4(rid))
    r = calc(f, lambda rid: pay_tri(rid, 1, 2, 3))
    if r and r["n"] >= 30 and r["rec"] >= 130:
        candidates.append((f"L4 × 風速{bin_label}", r))

# 強風 × アウト勢
for N in [3, 4, 5]:
    f = lambda rid, n=N: rid in race_weather and race_weather[rid][2] >= 5
    r = calc(f, lambda rid, n=N: pay_win(rid, n))
    if r and r["n"] >= 30 and r["rec"] >= 130:
        candidates.append((f"強風×{N}号艇単勝", r))

# 波高 × 1号艇
for bin_label in ["0-1", "2-3", "4-5", "6+"]:
    f = lambda rid, bl=bin_label: rid in race_weather and wave_bin(race_weather[rid][3]) == bl
    for bet_name, pay_fn in [("3連単1-2-3", lambda r: pay_tri(r, 1, 2, 3))]:
        r = calc(f, pay_fn)
        if r and r["n"] >= 30 and r["rec"] >= 130:
            candidates.append((f"波高{bin_label} × {bet_name}", r))

candidates.sort(key=lambda x: -x[1]["rec"])
if candidates:
    print(f"\n  {'戦略':<28} n     HIT% 回収率   損益")
    for name, r in candidates[:20]:
        print(f"  {name:<28} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
              f"{r['rec']:>6.1f}% {r['profit']:>+9,}円")
else:
    print("\n  (回収率>=130% の戦略なし)")

conn.close()
