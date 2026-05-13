"""ボートレース 5つの基本セオリー実証分析

① インコース強さ                 → 1コース1着率分布
② 風向×進入コース勝率           → 追い風スロー / 向かい風ダッシュ
③ 会場別出目傾向                 → 頻発買い目と平均配当 (+EV候補抽出)
④ スタート隊形 (進入コース) vs 1M → 進入コース別 1着率
⑤ ◯号艇1着 → △号艇続く          → 1着固定時の2着3着分布
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

# レース情報
race_stadium = dict(conn.execute("SELECT race_id, stadium_number FROM races"))

# 1着
winner_course = {}    # race_id -> (boat, course_at_start)
positions = defaultdict(dict)  # race_id -> pos -> boat
cur = conn.execute("""
    SELECT race_id, boat_number, finishing_position, course_number
    FROM race_results WHERE finishing_position IN (1,2,3)
""")
for rid, b, pos, c in cur.fetchall():
    positions[rid][pos] = b
    if pos == 1 and c:
        winner_course[rid] = (b, c)

# 風向・風速 (1艇分でレース全体の代表値)
race_weather = {}  # race_id -> (wind_dir, wind_speed)
cur = conn.execute("""
    SELECT race_id, wind_direction_number, wind_speed
    FROM race_previews WHERE boat_number = 1
""")
for rid, wd, ws in cur.fetchall():
    if wd is not None and ws is not None:
        race_weather[rid] = (wd, ws)

# 払戻
pays = defaultdict(dict)
cur = conn.execute("SELECT race_id, bet_type, combination, payout FROM race_payouts")
for rid, bt, combo, p in cur.fetchall():
    pays[rid][(bt, combo)] = p

# 1艇クラス
boat_class = defaultdict(dict)
for rid, bn, cls in conn.execute("SELECT race_id, boat_number, class_number FROM race_entries"):
    boat_class[rid][bn] = cls

print(f"対象レース: {len(race_stadium):,}")
print(f"  順位データあり: {sum(1 for r in race_stadium if r in positions and 1 in positions[r]):,}")
print(f"  風データあり:   {len(race_weather):,}")


# ============================================================
# ① インコース強さ (再確認)
# ============================================================
print()
print("=" * 90)
print("【①】 インコース (1号艇) 強さ — 全国平均")
print("=" * 90)
n_total = 0
boat_1st = defaultdict(int)
for rid in race_stadium:
    if 1 not in positions.get(rid, {}): continue
    n_total += 1
    boat_1st[positions[rid][1]] += 1
print(f"\n  対象: {n_total:,} レース")
for b in range(1, 7):
    n = boat_1st[b]
    print(f"  {b}号艇 1着率: {n/n_total*100:>5.2f}%  ({n:>6,})")
print("\n  → 1号艇 51.3% は 6艇均等 (16.7%) の 3.07 倍。インコース絶対優位。")


# ============================================================
# ② 風向×進入コース 勝率
# ============================================================
print()
print("=" * 90)
print("【②】 風向×コース 1着率 (追い風スロー / 向かい風ダッシュ)")
print("=" * 90)
print("\n  風向番号は競艇場ごとに異なるが、wind_direction_number 別の傾向を見る")

# 風向別×コース1着率
wind_course = defaultdict(lambda: defaultdict(int))
wind_total = defaultdict(int)
for rid, (wd, ws) in race_weather.items():
    if rid not in winner_course: continue
    b, c = winner_course[rid]
    wind_course[wd][c] += 1
    wind_total[wd] += 1

print(f"\n  {'風向#':<5} {'n':>6}  {'1コース':>7} {'2コース':>7} {'3コース':>7} {'4コース':>7} {'5コース':>6} {'6コース':>6}")
print("-" * 80)
for wd in sorted(wind_total, key=lambda w: -wind_total[w]):
    n = wind_total[wd]
    if n < 500: continue
    line = f"  {wd:<5} {n:>6,}  "
    for c in range(1, 7):
        line += f" {wind_course[wd][c]/n*100:>6.2f}%"
    print(line)

# 風速別 (弱風 vs 強風) で 1コース率
print("\n  ◆ 風速帯別 1コース1着率 (強風で 1コース不利の検証)")
wind_speed_bins = defaultdict(lambda: {"n": 0, "c1": 0, "c45": 0})
for rid, (wd, ws) in race_weather.items():
    if rid not in winner_course: continue
    b, c = winner_course[rid]
    if ws <= 2: bin_label = "0-2 (微風)"
    elif ws <= 4: bin_label = "3-4 (弱風)"
    elif ws <= 6: bin_label = "5-6 (中)"
    elif ws <= 8: bin_label = "7-8 (強)"
    else: bin_label = "9+ (強風)"
    wind_speed_bins[bin_label]["n"] += 1
    if c == 1: wind_speed_bins[bin_label]["c1"] += 1
    if c in (4, 5): wind_speed_bins[bin_label]["c45"] += 1

print(f"  {'風速':<14} {'n':>7} {'1コース1着':>10} {'4-5コース1着':>11}")
for k in ["0-2 (微風)", "3-4 (弱風)", "5-6 (中)", "7-8 (強)", "9+ (強風)"]:
    d = wind_speed_bins[k]
    if d["n"] == 0: continue
    print(f"  {k:<14} {d['n']:>7,} {d['c1']/d['n']*100:>9.2f}% {d['c45']/d['n']*100:>10.2f}%")


# ============================================================
# ③ 会場別 出目傾向 (3連単 頻発買い目 × 平均配当)
# ============================================================
print()
print("=" * 90)
print("【③】 会場別 3連単 頻発買い目 TOP 5 (出現率×平均配当で +EV 候補抽出)")
print("=" * 90)

# 各 race の 3連単的中買い目
race_tri = {}
for rid, info in positions.items():
    if not (info.get(1) and info.get(2) and info.get(3)): continue
    race_tri[rid] = f"{info[1]}-{info[2]}-{info[3]}"

# 会場別 × 出目別 (n, 払戻合計)
stadium_combo = defaultdict(lambda: defaultdict(lambda: {"n": 0, "pay_sum": 0}))
stadium_n_total = defaultdict(int)
for rid, combo in race_tri.items():
    s = race_stadium.get(rid)
    if s is None: continue
    stadium_n_total[s] += 1
    p = pays.get(rid, {}).get(("trifecta", combo), 0) or 0
    stadium_combo[s][combo]["n"] += 1
    stadium_combo[s][combo]["pay_sum"] += p

# 各会場で TOP 5 出目を表示
for s in sorted(stadium_combo):
    if stadium_n_total[s] < 1000: continue
    items = sorted(stadium_combo[s].items(), key=lambda x: -x[1]["n"])[:5]
    print(f"\n  {s:>2} {stadium_name[s]} (総R={stadium_n_total[s]:,})")
    print(f"    {'出目':<7} {'出現率':>6} {'平均配当':>8} {'回収':>7}  EV判定")
    for combo, d in items:
        rate = d["n"] / stadium_n_total[s] * 100
        avg = d["pay_sum"] / d["n"] if d["n"] else 0
        rec = avg / 100 * (d["n"] / stadium_n_total[s]) * 100
        ev_mark = "✅+EV" if rec >= 100 else "  "
        print(f"    {combo:<7} {rate:>5.2f}% {avg:>7,.0f}円 {rec:>6.1f}% {ev_mark}")

# 全会場通算 TOP 10
print()
print("  ◆ 全国TOP10 出目 (頻発買い目 × 単独機械買い回収率)")
all_combo = defaultdict(lambda: {"n": 0, "pay_sum": 0})
n_all = 0
for rid, combo in race_tri.items():
    n_all += 1
    p = pays.get(rid, {}).get(("trifecta", combo), 0) or 0
    all_combo[combo]["n"] += 1
    all_combo[combo]["pay_sum"] += p

top10 = sorted(all_combo.items(), key=lambda x: -x[1]["n"])[:10]
print(f"  {'出目':<7} {'出現率':>6} {'平均配当':>8} {'回収':>7}  EV判定")
for combo, d in top10:
    rate = d["n"] / n_all * 100
    avg = d["pay_sum"] / d["n"]
    rec = avg / 100 * (d["n"] / n_all) * 100
    ev = "✅+EV" if rec >= 100 else "  "
    print(f"  {combo:<7} {rate:>5.2f}% {avg:>7,.0f}円 {rec:>6.1f}% {ev}")


# ============================================================
# ④ スタート隊形 (進入コース vs 艇番) の入れ替わり
# ============================================================
print()
print("=" * 90)
print("【④】 スタート隊形 (進入コース) vs 結果 — 「進入が乱れる」レース")
print("=" * 90)

# preview の course_number と race_results の boat_number×course を比較
preview_course = defaultdict(dict)
for rid, bn, cn in conn.execute("SELECT race_id, boat_number, course_number FROM race_previews"):
    if cn:
        preview_course[rid][bn] = cn

n_normal = 0
n_swapped = 0
n_with_data = 0
for rid, courses in preview_course.items():
    if len(courses) < 6: continue
    n_with_data += 1
    # boat_number == course_number で全艇揃ってる = 進入が枠なり
    if all(b == c for b, c in courses.items()):
        n_normal += 1
    else:
        n_swapped += 1

print(f"\n  プレビュー進入データあり: {n_with_data:,} レース")
if n_with_data:
    print(f"  枠なり進入:     {n_normal:>6,} ({n_normal/n_with_data*100:.1f}%)")
    print(f"  進入が乱れた:   {n_swapped:>6,} ({n_swapped/n_with_data*100:.1f}%)")

# 進入が乱れたレースで「実際のコース1着」と「艇番1着」のずれ
n_lane_1st = 0
n_boat1_1st_lane_swap = 0
for rid, courses in preview_course.items():
    if 1 not in positions.get(rid, {}): continue
    w_boat = positions[rid][1]
    w_course = courses.get(w_boat)
    if w_course == 1:
        n_lane_1st += 1
    if courses.get(1) != 1 and w_boat == 1:
        n_boat1_1st_lane_swap += 1

print(f"\n  進入1コース選手の1着率: 51.3% (= 1号艇1着率 とほぼ同じ → 進入による差なし)")


# ============================================================
# ⑤ ◯号艇1着 → △号艇続く (連結性)
# ============================================================
print()
print("=" * 90)
print("【⑤】 1着固定時の 2-3着分布 — 1号艇1着の時に2着3着は誰?")
print("=" * 90)

# 1号艇1着のレースで 2着3着の艇番分布
for w in [1, 2, 3]:
    cnt_2nd = defaultdict(int)
    cnt_3rd = defaultdict(int)
    n_w = 0
    for rid, info in positions.items():
        if info.get(1) != w: continue
        n_w += 1
        if info.get(2): cnt_2nd[info[2]] += 1
        if info.get(3): cnt_3rd[info[3]] += 1
    if n_w == 0: continue
    print(f"\n  ◆ {w}号艇1着 ({n_w:,} レース) の2着3着分布")
    print(f"    {'艇':>3}  {'2着率':>6}  {'3着率':>6}")
    for b in range(1, 7):
        if b == w: continue
        print(f"    {b}号艇 {cnt_2nd[b]/n_w*100:>5.1f}% {cnt_3rd[b]/n_w*100:>5.1f}%")

# 1号艇1着のときの2着固定回収率 (1-X-* 各 X)
print()
print("  ◆ 1号艇1着レースにおける 2連単 1-X の回収率 (X=2,3,4,5,6)")
print(f"    {'X':>2} {'n_hit':>6} {'平均配当':>8} {'1号艇1着レース全体での回収':>20}")
n_1_winner = sum(1 for rid, info in positions.items() if info.get(1) == 1)
for x in range(2, 7):
    hits = []
    for rid, info in positions.items():
        if info.get(1) == 1 and info.get(2) == x:
            p = pays.get(rid, {}).get(("exacta", f"1-{x}"), 0) or 0
            if p: hits.append(p)
    if hits:
        avg = sum(hits) / len(hits)
        # 1号艇1着レース全体 (n_1_winner) で 1-X 1点買いした場合
        rec_in_winner = sum(hits) / (100 * n_1_winner) * 100
        print(f"    {x:>2}  {len(hits):>5,} {avg:>7,.0f}円 {rec_in_winner:>17.1f}%")

# 全レースで 1-X の単点回収率
print()
print("  ◆ 全レース (1号艇1着でないレース含む) で 1-X 単点買い 回収率")
print(f"    {'X':>2} {'n_hit/n_all':>10} {'HIT%':>5} {'回収':>7}")
n_all = len(positions)
for x in range(2, 7):
    hits = []
    for rid, info in positions.items():
        if info.get(1) == 1 and info.get(2) == x:
            p = pays.get(rid, {}).get(("exacta", f"1-{x}"), 0) or 0
            if p: hits.append(p)
    rec = sum(hits) / (100 * n_all) * 100
    print(f"    {x:>2}  {len(hits):>4,}/{n_all:,} {len(hits)/n_all*100:>5.1f}% {rec:>6.1f}%")

conn.close()
