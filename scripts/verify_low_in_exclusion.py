"""桐生・江戸川・鳴門 を L4 除外に追加した場合の回収率変化を検証

ユーザー仮説:
  桐生・戸田・江戸川・平和島・鳴門 はインが逃げにくい
  → L4 戦略から除外すべきか?

現状の B除外: {2 戸田, 4 平和島, 7 蒲郡, 8 常滑, 10 三国, 19 下関, 21 芦屋, 24 大村}
新規候補:      {1 桐生, 3 江戸川, 14 鳴門}

検証内容:
  1. 候補3会場 各単独での L4 戦略 回収率 (1号艇A1 + 500-1000円本命)
  2. 全24会場の L4 戦略 回収率ランキング
  3. 現状除外 vs +3会場追加除外 の通算比較
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

# 会場情報
stadium_name = {}
stadium_in = {}
cur = conn.execute("SELECT stadium_number, name, in_strength FROM stadiums")
for n, name, instr in cur.fetchall():
    stadium_name[n] = name
    stadium_in[n] = instr

# レース×会場×グレード×本命オッズ
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

# 順位
cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position FROM race_results
    WHERE finishing_position IN (1,2,3)
""")
positions = defaultdict(dict)
for rid, bn, pos in cur.fetchall():
    positions[rid][pos] = bn

# 払戻
cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
pays = defaultdict(dict)
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p

# 1号艇クラス
cur = conn.execute("SELECT race_id, class_number FROM race_entries WHERE boat_number=1")
boat1_class = dict(cur.fetchall())


def bet_tri_123(rid):
    if (positions[rid].get(1) == 1 and positions[rid].get(2) == 2
            and positions[rid].get(3) == 3):
        return pays[rid].get(("trifecta", "1-2-3"), 0) or 0
    return 0


def bet_win1(rid):
    return pays[rid].get(("win", "1"), 0) or 0 if positions[rid].get(1) == 1 else 0


def bet_exa_12(rid):
    if positions[rid].get(1) == 1 and positions[rid].get(2) == 2:
        return pays[rid].get(("exacta", "1-2"), 0) or 0
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
    return (rois[int(N_BOOT * 0.025)] * 100 + 100,
            rois[int(N_BOOT * 0.975)] * 100 + 100,
            sum(1 for r in rois if r > 0) / N_BOOT)


def calc_strategy(filter_fn, label, bet_fn=bet_tri_123, bet_label="3連単1-2-3"):
    bets = []
    for rid, info in race_info.items():
        if not filter_fn(rid, info):
            continue
        bets.append(bet_fn(rid))
    n = len(bets)
    if n == 0:
        return None
    n_hit = sum(1 for b in bets if b > 0)
    profit = sum(bets) - 100 * n
    rec = sum(bets) / max(1, 100 * n) * 100
    lo, hi, p_pos = bootstrap_ci(bets)
    return {
        "label": label, "bet_label": bet_label,
        "n": n, "hit": n_hit, "rec": rec, "profit": profit,
        "ci_lo": lo, "ci_hi": hi, "p_pos": p_pos,
    }


# 既存 B除外
EXCLUDE_CURRENT = {2, 4, 7, 8, 10, 19, 21, 24}
# 追加候補
NEW_LOW_IN = {1, 3, 14}  # 桐生, 江戸川, 鳴門
EXCLUDE_PROPOSED = EXCLUDE_CURRENT | NEW_LOW_IN

print("=" * 90)
print("インが逃げにくい会場 (桐生・江戸川・鳴門) を L4 除外に加えるか検証")
print("=" * 90)
print()
print(f"現状 B除外 ({len(EXCLUDE_CURRENT)}):", ", ".join(f"{n} {stadium_name[n]}" for n in sorted(EXCLUDE_CURRENT)))
print(f"新規候補 ({len(NEW_LOW_IN)}):     ", ", ".join(f"{n} {stadium_name[n]}" for n in sorted(NEW_LOW_IN)))
print()

# 1. 全24会場の L4 戦略 (3連単1-2-3) 回収率ランキング (A1 1号艇限定)
print("【1】 全24会場 L4 戦略 回収率ランキング (本命500-1000円, 1号艇A1, 3連単1-2-3)")
print("-" * 90)
results_per_stadium = []
for s in range(1, 25):
    info_fn = lambda rid, info, s=s: (
        info["stadium"] == s
        and 500 <= info["fav_payout"] < 1000
        and boat1_class.get(rid) == 1  # A1
    )
    r = calc_strategy(info_fn, f"{s} {stadium_name[s]} ({stadium_in[s]})")
    if r and r["n"] >= 10:
        results_per_stadium.append((s, r))

results_per_stadium.sort(key=lambda x: -x[1]["rec"])
print(f"  {'会場':<22} {'in':<10} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>10} {'除外状態'}")
for s, r in results_per_stadium:
    instr = stadium_in[s]
    current = "現除外" if s in EXCLUDE_CURRENT else ("候補追加" if s in NEW_LOW_IN else "")
    mark = "❌" if s in EXCLUDE_CURRENT else ("⚠️" if s in NEW_LOW_IN else "")
    print(f"  {s:>2} {stadium_name[s]:<18} {instr:<10} "
          f"{r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
          f"{r['rec']:>6.1f}% [{r['ci_lo']:>6.1f},{r['ci_hi']:>6.1f}] "
          f"{r['profit']:>+9,}円 {mark} {current}")
print()

# 2. 候補3会場の詳細
print("【2】 新規候補 3 会場の詳細 (1号艇A1限定)")
print("-" * 90)
for s in sorted(NEW_LOW_IN):
    f = lambda rid, info, s=s: (
        info["stadium"] == s
        and 500 <= info["fav_payout"] < 1000
        and boat1_class.get(rid) == 1
    )
    r_tri = calc_strategy(f, "tri", bet_tri_123, "3連単1-2-3")
    r_win = calc_strategy(f, "win", bet_win1, "単勝1")
    r_exa = calc_strategy(f, "exa", bet_exa_12, "2連単1-2")
    print(f"\n  ■ {s} {stadium_name[s]} ({stadium_in[s]})")
    for r, name in [(r_win, "単勝1"), (r_exa, "2連単1-2"), (r_tri, "3連単1-2-3")]:
        if r:
            print(f"    {name:<12}: n={r['n']:>3} HIT={r['hit']/r['n']*100:>5.1f}% "
                  f"回収={r['rec']:>6.1f}% CI=[{r['ci_lo']:>6.1f},{r['ci_hi']:>6.1f}] "
                  f"損益={r['profit']:>+8,}円")

# 3. 通算比較
print()
print("【3】 通算回収率 比較 (1号艇A1 + 500-1000円本命)")
print("-" * 90)

cases = [
    ("除外なし",       lambda s: True),
    ("現状B除外",       lambda s: s not in EXCLUDE_CURRENT),
    ("現状+桐生のみ",   lambda s: s not in (EXCLUDE_CURRENT | {1})),
    ("現状+江戸川のみ", lambda s: s not in (EXCLUDE_CURRENT | {3})),
    ("現状+鳴門のみ",   lambda s: s not in (EXCLUDE_CURRENT | {14})),
    ("現状+3会場追加",  lambda s: s not in EXCLUDE_PROPOSED),
]

for bet_fn, bet_name in [(bet_tri_123, "3連単1-2-3"), (bet_exa_12, "2連単1-2"), (bet_win1, "単勝1")]:
    print(f"\n  ◆ {bet_name}")
    print(f"  {'シナリオ':<20} {'n':>5} {'HIT%':>6} {'回収':>7} {'CI 95%':>20} {'損益':>11}")
    for label, fil in cases:
        ff = lambda rid, info, fil=fil: (
            fil(info["stadium"])
            and 500 <= info["fav_payout"] < 1000
            and boat1_class.get(rid) == 1
        )
        r = calc_strategy(ff, label, bet_fn, bet_name)
        if r:
            mark = "[200+]" if r["rec"] >= 200 else "[150+]" if r["rec"] >= 150 else "[130+]" if r["rec"] >= 130 else ""
            print(f"  {label:<18} {r['n']:>5,} {r['hit']/r['n']*100:>5.1f}% "
                  f"{r['rec']:>6.1f}% [{r['ci_lo']:>6.1f},{r['ci_hi']:>6.1f}] "
                  f"{r['profit']:>+10,}円 {mark}")

print()
print("=" * 90)
print("判定基準: 新規除外3会場の n が十分 (>50) で 回収率 < 100% (損失方向) なら追加除外で期待値↑")
print("=" * 90)

conn.close()
