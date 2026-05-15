"""決まり手 (kimarite) 分析

ユーザー提示の数値と実データを比較し、決まり手×会場のクロス集計から
戦略の活路を探る。

検証内容:
  1. 実データのコース×決まり手 行列 (ユーザー提示と照合)
  2. 会場×コース×決まり手 のクロス集計 (どの会場で何の決まり手が多いか)
  3. 「差しが多い会場 × 差し展開」の絞り込みで EV+ 取れるか
  4. 1号艇1着で「逃げ」以外の決まり手 (抜き等) があるレースの傾向
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect("data/boatrace.db")
stadium_name = {n: name for n, name in conn.execute("SELECT stadium_number, name FROM stadiums")}

# レース順位 + 決まり手 (1着のみ kimarite が記録される)
race_stadium = dict(conn.execute("SELECT race_id, stadium_number FROM races"))

# 1着 = boat (winner), course (起ち位置, course_number), kimarite
cur = conn.execute("""
    SELECT rr.race_id, rr.boat_number, rr.course_number, rr.kimarite
    FROM race_results rr
    WHERE rr.finishing_position = 1
""")
winner_info = {}  # race_id -> (boat, course, kimarite)
for rid, b, c, k in cur.fetchall():
    winner_info[rid] = (b, c, k)


# ============================================================
# 1. コース別 決まり手分布 (全会場)
# ============================================================
print("=" * 90)
print("【1】 コース別 決まり手分布 (実データ全会場、過去10ヶ月)")
print("=" * 90)
course_kimarite = defaultdict(lambda: defaultdict(int))
course_total = defaultdict(int)
all_kimarite = set()
for rid, (b, c, k) in winner_info.items():
    if c is None or k is None: continue
    course_kimarite[c][k] += 1
    course_total[c] += 1
    all_kimarite.add(k)

# 決まり手の出現頻度順に列を並べる
total_per_k = defaultdict(int)
for c, dd in course_kimarite.items():
    for k, n in dd.items():
        total_per_k[k] += n
ordered_k = sorted(all_kimarite, key=lambda k: -total_per_k[k])

print(f"\n  実データの決まり手種類: {', '.join(ordered_k)}")
print()
print(f"  {'コース':<6} {'n':>6}  " + "  ".join(f"{k:<8}" for k in ordered_k))
print("-" * 90)
for c in range(1, 7):
    n = course_total[c]
    if n == 0: continue
    line = f"  {c}コース  {n:>5,}  "
    for k in ordered_k:
        pct = course_kimarite[c][k] / n * 100
        line += f" {pct:>6.2f}% "
    print(line)

print("\n  (ユーザー提示) 1: 逃げ97.2/抜2.7 | 2: まくり29.2/差し61.7/抜8.9 | 3: まくり30.7/差し6.1/まくり差し52.3/抜9.2/恵1.5 | ...")

# ============================================================
# 2. 会場×コース×決まり手 (1コース「抜かれ率」の高い会場)
# ============================================================
print()
print("=" * 90)
print("【2】 各会場で「1コース以外が1着」の割合 + 決まり手内訳")
print("=" * 90)

stadium_course_kim = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
stadium_n_races = defaultdict(int)
for rid, (b, c, k) in winner_info.items():
    s = race_stadium.get(rid)
    if s is None or c is None or k is None: continue
    stadium_course_kim[s][c][k] += 1
    stadium_n_races[s] += 1

print(f"\n  {'会場':<7} {'総R':>5}  {'1コース':>6}  {'2差し':>6} {'3まくり差し':>9} {'4まくり':>7} {'外3+':>6}")
print("-" * 80)

# 「1コース以外1着率」「2差し率」「3まくり差し率」 でランキング
rank = []
for s in range(1, 25):
    if stadium_n_races[s] == 0: continue
    n_total = stadium_n_races[s]
    n_1c = sum(stadium_course_kim[s][1].values())
    n_2c_sashi = stadium_course_kim[s][2].get("差し", 0)
    n_3c_makuri_sashi = stadium_course_kim[s][3].get("まくり差し", 0)
    n_4c_makuri = stadium_course_kim[s][4].get("まくり", 0)
    n_3plus = sum(sum(stadium_course_kim[s][c].values()) for c in range(3, 7))
    rank.append((s, n_total, n_1c/n_total*100, n_2c_sashi/n_total*100,
                 n_3c_makuri_sashi/n_total*100, n_4c_makuri/n_total*100,
                 n_3plus/n_total*100))

# 1コース1着率 低い順 (荒れる会場)
rank.sort(key=lambda x: x[2])
for s, n, p1, p2s, p3m, p4m, p3p in rank:
    mark = "🔴" if p1 < 50 else ("🟢" if p1 >= 56 else "  ")
    print(f"  {mark} {s:>2} {stadium_name[s]:<5} {n:>5,}  "
          f"{p1:>5.2f}%  {p2s:>5.2f}%  {p3m:>8.2f}%  {p4m:>6.2f}%  {p3p:>5.2f}%")


# ============================================================
# 3. 1コース1着の決まり手内訳 (抜き = 1号艇が一度遅れて差し返した? 恵まれ?)
# ============================================================
print()
print("=" * 90)
print("【3】 1コース1着の決まり手内訳 — 「逃げ」以外があったレースの会場分布")
print("=" * 90)

c1_kim = defaultdict(lambda: defaultdict(int))  # stadium -> kimarite -> count
for rid, (b, c, k) in winner_info.items():
    if c != 1: continue
    s = race_stadium.get(rid)
    if s is None: continue
    c1_kim[s][k] += 1

print(f"\n  {'会場':<7} {'1着総R':>6}  {'逃げ%':>6} {'抜き%':>6} {'差し%':>6} {'恵%':>5} {'他%':>5}")
print("-" * 80)
for s in sorted(c1_kim, key=lambda s: -c1_kim[s].get("逃げ", 0) / max(1, sum(c1_kim[s].values()))):
    total = sum(c1_kim[s].values())
    if total < 100: continue
    p_nige = c1_kim[s].get("逃げ", 0) / total * 100
    p_nuki = c1_kim[s].get("抜き", 0) / total * 100
    p_sashi = c1_kim[s].get("差し", 0) / total * 100
    p_megu = c1_kim[s].get("恵まれ", 0) / total * 100
    p_other = 100 - p_nige - p_nuki - p_sashi - p_megu
    mark = "🟢" if p_nige >= 97 else "🔴" if p_nige < 95 else "  "
    print(f"  {mark} {s:>2} {stadium_name[s]:<5} {total:>5,}  "
          f"{p_nige:>5.2f}%  {p_nuki:>5.2f}%  {p_sashi:>5.2f}%  {p_megu:>4.2f}%  {p_other:>4.2f}%")


# ============================================================
# 4. 戦略示唆: 「逃げ」が少ない会場 = 1号艇本命買いの危険度
# ============================================================
print()
print("=" * 90)
print("【4】 戦略示唆: 「逃げ率」が低い会場 = 1号艇逃げきり率が低い")
print("=" * 90)
print("\n本検証は L4 戦略 (1号艇A1 + 本命500-1000円 + 3連単1-2-3) と整合的か?")
print("「逃げ率が低い会場」を L4 から除外すれば回収率が上がるはず")
print()
print("ただし前回検証では:")
print("  - 戸田 (逃げ最少クラスのはず) → L4 で 207.5% (★トップ)")
print("  - 江戸川 (逃げ少ない) → L4 で 198.8%")
print("  - 桐生 (逃げ多めのはず) → L4 で 119.6% (むしろ低調)")
print("→ 単純な「逃げ率」と L4 回収率は逆相関に近い。")
print("  オッズが本命500-1000円帯にハマる会場 (=人気と実力に微妙な乖離がある)")
print("  こそが L4 の収益源で、「逃げ率の絶対値」ではない。")

conn.close()
