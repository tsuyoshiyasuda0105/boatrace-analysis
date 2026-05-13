"""1コース以外を1着とする戦略の本格バックテスト

ユーザー提示の知見:
  2コース1着: 江戸川/平和島/鳴門
  3コース1着: 戸田/福岡/鳴門/江戸川
  4コース1着: 戸田/平和島/桐生/蒲郡
  5コース1着: 平和島/鳴門/戸田/桐生
  6コース1着: 平和島/江戸川/戸田

機械的単勝買いでは全部 -EV だったが、配当の大きい買い目 + 選手クラス絞り込みで
+EV になる組み合わせを探す。

検証する買い目:
  ① 単勝 N (再掲、ベースライン)
  ② 2連単 N-X BOX (Nを1着固定、2着を全候補)
  ③ 3連単 N-X-Y 頭固定 BOX (1着のみ固定)
  ④ 3連単 N-X-Y 頭+ヒモ固定 (Nに対する1コース2着、N-1-? か N-2-?)
  ⑤ N号艇A1 限定 (選手クラス絞り込み)
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

# レース順位
positions = defaultdict(dict)
cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position FROM race_results
    WHERE finishing_position IN (1,2,3)
""")
for rid, bn, pos in cur.fetchall():
    positions[rid][pos] = bn

# レース会場
race_stadium = dict(conn.execute("SELECT race_id, stadium_number FROM races"))

# 選手クラス
boat_class = defaultdict(dict)  # race_id -> boat -> class
cur = conn.execute("SELECT race_id, boat_number, class_number FROM race_entries")
for rid, bn, cls in cur.fetchall():
    boat_class[rid][bn] = cls

# 払戻
pays = defaultdict(dict)
cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p


def bootstrap_ci(payouts, n_bet_per_race=1):
    """1レース当たりの正味払戻 (合計 - n_bet_per_race*100) から CI を出す。
    payouts は「1レース1枠 (n_bet_per_race点を買って戻ってきた合計)」のリスト。"""
    n = len(payouts)
    if n == 0: return None, None, None
    rois = []
    cost_per_race = 100 * n_bet_per_race
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rec = sum(sample) / (cost_per_race * n) * 100
        rois.append(rec)
    rois.sort()
    return rois[int(N_BOOT * 0.025)], rois[int(N_BOOT * 0.975)], sum(1 for r in rois if r > 100) / N_BOOT


def calc(filter_fn, payout_fn, n_bet_per_race=1, label=""):
    """filter_fn: race_id -> bool で判定
       payout_fn: race_id -> 払戻合計 (1点100円 × n_bet_per_race を購入したと仮定)"""
    payouts = []
    for rid in race_stadium:
        if not filter_fn(rid): continue
        payouts.append(payout_fn(rid))
    n = len(payouts)
    if n == 0: return None
    cost = 100 * n_bet_per_race * n
    total = sum(payouts)
    hit = sum(1 for p in payouts if p > 0)
    rec = total / max(1, cost) * 100
    profit = total - cost
    lo, hi, p_pos = bootstrap_ci(payouts, n_bet_per_race)
    return {
        "label": label, "n": n, "hit": hit, "n_bet_per_race": n_bet_per_race,
        "rec": rec, "profit": profit, "ci_lo": lo, "ci_hi": hi, "p_pos": p_pos,
    }


def fmt(r):
    if r is None: return "(n=0)"
    return (f"n={r['n']:>4,} pts/R={r['n_bet_per_race']:>2} "
            f"HIT={r['hit']/r['n']*100:>5.1f}% "
            f"回収={r['rec']:>6.1f}% "
            f"CI=[{r['ci_lo']:>6.1f},{r['ci_hi']:>6.1f}] "
            f"P+={r['p_pos']*100:>5.1f}% "
            f"損益={r['profit']:>+9,}円")


# ============================================================
# テスト対象: 仮説の会場×コース組み合わせ
# ============================================================
TARGETS = [
    (2, [3, 4, 14], "江戸川/平和島/鳴門"),
    (3, [2, 22, 14, 3], "戸田/福岡/鳴門/江戸川"),
    (4, [2, 4, 1, 7], "戸田/平和島/桐生/蒲郡"),
    (5, [4, 14, 2, 1], "平和島/鳴門/戸田/桐生"),
    (6, [4, 3, 2], "平和島/江戸川/戸田"),
]


def is_target(rid, course, stadiums):
    """会場 in 仮説リスト ∧ ?号艇がNコース"""
    return race_stadium.get(rid) in stadiums


# ============================================================
# 1. 単勝 N (再掲)
# ============================================================
print("=" * 110)
print("【1】 単勝 N (該当会場で N号艇単勝を買い続ける)")
print("=" * 110)
for course, stadiums, label in TARGETS:
    def f(rid, st=set(stadiums), c=course):
        return race_stadium.get(rid) in st
    def p(rid, c=course):
        if positions[rid].get(1) == c:
            return pays[rid].get(("win", str(c)), 0) or 0
        return 0
    r = calc(f, p, 1, f"{course}号艇 単勝 @ {label}")
    print(f"  {course}号艇 @ {label:<28} {fmt(r)}")


# ============================================================
# 2. 2連単 N-X (Nを1着固定、2着は全候補 5点買い)
# ============================================================
print()
print("=" * 110)
print("【2】 2連単 N-(他5艇) BOX (Nを頭固定、2着は全5艇に流す、1Rで5点)")
print("=" * 110)
for course, stadiums, label in TARGETS:
    def f(rid, st=set(stadiums)):
        return race_stadium.get(rid) in st
    def p(rid, c=course):
        if positions[rid].get(1) != c:
            return 0
        b2 = positions[rid].get(2)
        if b2:
            return pays[rid].get(("exacta", f"{c}-{b2}"), 0) or 0
        return 0
    r = calc(f, p, 5, f"{course}-* 5点 @ {label}")
    print(f"  {course}-(他) @ {label:<28} {fmt(r)}")


# ============================================================
# 3. 3連単 N-X-Y 頭固定 BOX (1着のみ固定、2-3着は他5艇から2点ずつ 20点)
# ============================================================
print()
print("=" * 110)
print("【3】 3連単 N-*-* (Nを頭固定、2-3着は他5艇からのBOX、1Rで20点)")
print("=" * 110)
for course, stadiums, label in TARGETS:
    def f(rid, st=set(stadiums)):
        return race_stadium.get(rid) in st
    def p(rid, c=course):
        if positions[rid].get(1) != c:
            return 0
        b2, b3 = positions[rid].get(2), positions[rid].get(3)
        if b2 and b3 and b2 != c and b3 != c:
            return pays[rid].get(("trifecta", f"{c}-{b2}-{b3}"), 0) or 0
        return 0
    r = calc(f, p, 20, f"{course}-*-* 20点 @ {label}")
    print(f"  {course}-*-* @ {label:<28} {fmt(r)}")


# ============================================================
# 4. 3連単 N-1-* (Nを頭固定、2着は1号艇固定、3着は他4艇 4点)
# ============================================================
print()
print("=" * 110)
print("【4】 3連単 N-1-* (Nが1着で1号艇が差されて2着、3着は他4艇、1Rで4点)")
print("=" * 110)
for course, stadiums, label in TARGETS:
    if course == 1: continue
    def f(rid, st=set(stadiums)):
        return race_stadium.get(rid) in st
    def p(rid, c=course):
        if positions[rid].get(1) != c or positions[rid].get(2) != 1:
            return 0
        b3 = positions[rid].get(3)
        if b3 and b3 not in (c, 1):
            return pays[rid].get(("trifecta", f"{c}-1-{b3}"), 0) or 0
        return 0
    r = calc(f, p, 4, f"{course}-1-* 4点 @ {label}")
    print(f"  {course}-1-* @ {label:<28} {fmt(r)}")


# ============================================================
# 5. N号艇A1限定: 単勝・2連単・3連単
# ============================================================
print()
print("=" * 110)
print("【5】 N号艇クラス=A1 のレースに絞った場合")
print("=" * 110)
for course, stadiums, label in TARGETS:
    def f(rid, st=set(stadiums), c=course):
        if race_stadium.get(rid) not in st: return False
        return boat_class.get(rid, {}).get(c) == 1  # A1
    def p_win(rid, c=course):
        if positions[rid].get(1) == c:
            return pays[rid].get(("win", str(c)), 0) or 0
        return 0
    def p_exa(rid, c=course):
        if positions[rid].get(1) != c:
            return 0
        b2 = positions[rid].get(2)
        if b2:
            return pays[rid].get(("exacta", f"{c}-{b2}"), 0) or 0
        return 0
    r_win = calc(f, p_win, 1, f"単勝(N)")
    r_exa = calc(f, p_exa, 5, f"N-* 5点")
    if r_win and r_win["n"] > 50:
        print(f"  {course}号艇A1 @ {label:<22}")
        print(f"      単勝       {fmt(r_win)}")
        print(f"      2連単 N-*5 {fmt(r_exa)}")


# ============================================================
# 6. 該当会場全体での「N号艇が1着 vs 1号艇が1着」の発生比率
# ============================================================
print()
print("=" * 110)
print("【6】 該当会場での N号艇1着 vs 1号艇1着 の出現比 (頻度比較)")
print("=" * 110)
for course, stadiums, label in TARGETS:
    n_total = 0
    n_N_win = 0
    n_1_win = 0
    for rid in race_stadium:
        if race_stadium[rid] not in stadiums: continue
        if rid not in positions: continue
        n_total += 1
        w = positions[rid].get(1)
        if w == course: n_N_win += 1
        if w == 1: n_1_win += 1
    if n_total:
        print(f"  {course}号艇 @ {label:<28} "
              f"対象R={n_total:>5,} | "
              f"N号艇1着 {n_N_win/n_total*100:>5.1f}% / "
              f"1号艇1着 {n_1_win/n_total*100:>5.1f}% | "
              f"比 1:{n_1_win/max(1,n_N_win):.2f}")


conn.close()
