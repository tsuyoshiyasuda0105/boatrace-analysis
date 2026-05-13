"""会場×コース別 1着率分析 + 単勝/2連単戦略の期待値検証

ユーザー提示の仮説:
  2コース1着: 江戸川・平和島・鳴門
  3コース1着: 戸田・福岡・鳴門・江戸川
  4コース1着: 戸田・平和島・桐生・蒲郡
  5コース1着: 平和島・鳴門・戸田・桐生
  6コース1着: 平和島・江戸川・戸田

検証:
  1. 全24会場×6コースの1着率マトリクス (実データ)
  2. ユーザー仮説の会場で実際に該当コースが他会場より高い1着率か
  3. 各会場の「2コース単勝」などの期待値 (単純買い目で +EV ある会場を探す)
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

conn = sqlite3.connect("data/boatrace.db")

# 会場名
stadium_name = {}
cur = conn.execute("SELECT stadium_number, name FROM stadiums")
for n, name in cur.fetchall():
    stadium_name[n] = name

# レース・順位
cur = conn.execute("""
    SELECT r.race_id, r.stadium_number, rr.boat_number, rr.finishing_position
    FROM races r
    JOIN race_results rr ON r.race_id = rr.race_id
    WHERE rr.finishing_position = 1
""")
# (stadium, boat) → 1着回数 / レース回数
stadium_boat_wins = defaultdict(int)
cur_races = conn.execute("SELECT race_id, stadium_number FROM races")
stadium_n_races = defaultdict(int)
for rid, s in cur_races.fetchall():
    stadium_n_races[s] += 1

# 改めて1着の boat_number 集計
cur = conn.execute("""
    SELECT r.stadium_number, rr.boat_number, COUNT(*)
    FROM races r
    JOIN race_results rr ON r.race_id = rr.race_id
    WHERE rr.finishing_position = 1
    GROUP BY r.stadium_number, rr.boat_number
""")
for s, b, n in cur.fetchall():
    stadium_boat_wins[(s, b)] = n

# 単勝・2連単オッズ
cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
pays = defaultdict(dict)
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p

# 各レースの会場と1着
cur = conn.execute("""
    SELECT r.race_id, r.stadium_number, rr.boat_number
    FROM races r
    JOIN race_results rr ON r.race_id = rr.race_id
    WHERE rr.finishing_position = 1
""")
race_stadium_winner = {}
for rid, s, b in cur.fetchall():
    race_stadium_winner[rid] = (s, b)


# ============================================================
# 1. 全会場×コース 1着率マトリクス
# ============================================================
print("=" * 110)
print("【1】 全24会場×6コース 1着率マトリクス (過去10ヶ月)")
print("=" * 110)
print(f"\n  {'会場':<8} {'総R':>6}  {'1コース':>7} {'2コース':>7} {'3コース':>7} {'4コース':>7} {'5コース':>7} {'6コース':>7}")
print("-" * 80)

# 各会場の各コースの平均 (全会場平均) を計算
avg_rate_by_course = defaultdict(list)
table = []
for s in range(1, 25):
    n_total = stadium_n_races[s]
    if n_total == 0:
        continue
    rates = []
    for b in range(1, 7):
        n_win = stadium_boat_wins.get((s, b), 0)
        rate = n_win / n_total * 100 if n_total else 0
        rates.append(rate)
        avg_rate_by_course[b].append(rate)
    table.append((s, n_total, rates))

# 全会場平均 (リファレンス)
avg_per_course = {b: sum(rates) / len(rates) for b, rates in avg_rate_by_course.items()}

# 表示
for s, n_total, rates in table:
    line = f"  {s:>2} {stadium_name[s]:<6} {n_total:>6,}  "
    for b in range(1, 7):
        rate = rates[b - 1]
        avg = avg_per_course[b]
        diff = rate - avg
        # ±2% 以上ずれていたらマーキング
        if diff > 3:
            mark = "⬆"
        elif diff < -3:
            mark = "⬇"
        else:
            mark = " "
        line += f" {rate:>5.1f}%{mark}"
    print(line)

print(f"  {'平均':<10} {' ':>6}  ", end="")
for b in range(1, 7):
    print(f" {avg_per_course[b]:>5.1f}% ", end="")
print()

# ============================================================
# 2. ユーザー仮説検証
# ============================================================
print()
print("=" * 110)
print("【2】 ユーザー仮説の検証 (該当会場×コース vs 全会場平均)")
print("=" * 110)

hypotheses = {
    2: ("江戸川/平和島/鳴門", [3, 4, 14]),
    3: ("戸田/福岡/鳴門/江戸川", [2, 22, 14, 3]),
    4: ("戸田/平和島/桐生/蒲郡", [2, 4, 1, 7]),
    5: ("平和島/鳴門/戸田/桐生", [4, 14, 2, 1]),
    6: ("平和島/江戸川/戸田", [4, 3, 2]),
}

for course, (label, stadiums) in hypotheses.items():
    print(f"\n■ {course}コース1着 — 仮説会場: {label}")
    avg = avg_per_course[course]
    print(f"   全会場平均 {course}コース1着率: {avg:.2f}%")
    print(f"   {'会場':<8} {'1着率':>7} {'差':>7}  判定")
    for s in stadiums:
        n_total = stadium_n_races[s]
        if n_total == 0:
            continue
        rate = stadium_boat_wins.get((s, course), 0) / n_total * 100
        diff = rate - avg
        verdict = (
            "✅ 仮説どおり 高い" if diff > 1.5
            else "⚠️ 平均並み" if abs(diff) <= 1.5
            else "❌ 平均より低い"
        )
        print(f"   {s:>2} {stadium_name[s]:<6} {rate:>6.2f}% {diff:>+5.2f}pt  {verdict}")

# ============================================================
# 3. 単勝期待値 (各会場×コース、単勝買って勝つ場合の回収率)
# ============================================================
print()
print("=" * 110)
print("【3】 各会場×コース 単勝戦略の通期回収率 (n>=300 で有意)")
print("=" * 110)

# 各会場×コース で、そのコースが1着になった時の単勝払戻を集計
ev_table = defaultdict(lambda: {"n": 0, "hit": 0, "payout_sum": 0})
for rid, (s, w) in race_stadium_winner.items():
    for b in range(1, 7):
        ev_table[(s, b)]["n"] += 1
        if w == b:
            payout = pays[rid].get(("win", str(b)), 0)
            if payout:
                ev_table[(s, b)]["hit"] += 1
                ev_table[(s, b)]["payout_sum"] += payout

# ユーザー仮説に含まれる会場×コースだけ表示
print(f"\n  {'会場':<8} {'コース':<6} {'n':>5} {'HIT%':>6} {'回収':>7} {'損益':>10}")
print("-" * 60)

interesting_combos = []
for course, (_, stadiums) in hypotheses.items():
    for s in stadiums:
        d = ev_table[(s, course)]
        if d["n"] == 0:
            continue
        rec = d["payout_sum"] / max(1, 100 * d["n"]) * 100
        profit = d["payout_sum"] - 100 * d["n"]
        interesting_combos.append((s, course, d["n"], d["hit"], rec, profit))

# 期待値高い順
interesting_combos.sort(key=lambda x: -x[4])
for s, c, n, hit, rec, profit in interesting_combos:
    mark = "[+EV]" if rec >= 100 else ""
    print(f"  {s:>2} {stadium_name[s]:<6} {c}コース  {n:>5,} {hit/n*100:>5.1f}% "
          f"{rec:>6.1f}% {profit:>+9,}円 {mark}")

# ============================================================
# 4. 1コース弱い会場の確認 (L4 への示唆)
# ============================================================
print()
print("=" * 110)
print("【4】 1コース1着率ランキング (L4 戦略への影響)")
print("=" * 110)
print(f"\n  {'順位':>3} {'会場':<8} {'1コース1着率':>10}")
print("-" * 50)

rank = sorted(
    [(s, stadium_boat_wins.get((s, 1), 0) / max(1, stadium_n_races[s]) * 100)
     for s in range(1, 25) if stadium_n_races[s] > 0],
    key=lambda x: -x[1]
)
for i, (s, rate) in enumerate(rank, 1):
    mark = "🔴 1コース弱い" if rate < 50 else "🟢 1コース強い" if rate >= 60 else ""
    user_excl_now = s in {2, 4, 7, 8, 10, 19, 21, 24}
    excl_mark = " (B除外中)" if user_excl_now else ""
    print(f"  {i:>3}位 {s:>2} {stadium_name[s]:<6} {rate:>8.2f}%  {mark}{excl_mark}")

conn.close()
