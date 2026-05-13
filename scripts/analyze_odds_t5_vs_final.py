"""T-5min と T-1min (締切直前 ≒ 確定オッズ) の差分析

T-5min で配信した予測が、実際の締切時オッズとどれだけ乖離するかを集計。
L4 メール送信時 (T-5min) と「投票後の確定オッズ」の差を可視化する。

注: snapshot_label='final' は旧スキーマで付与されたラベル。新しい T-5/T-1 系統と
共存していないため、現状は T-1min (締切1分前) を実質 final として比較する。

実行:
  .venv\\Scripts\\python.exe scripts\\analyze_odds_t5_vs_final.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from src.db.connection import connect


def main():
    conn = connect()

    print("=== T-5min vs T-1min (締切直前 ≒ 確定オッズ) 差分析 ===\n")

    # T-5min と final の両方を持つ race × combination
    is_pg = hasattr(conn, "_kind") and conn._kind == "postgres"
    sql = """
        SELECT o5.race_id, o5.combination, o5.odds AS o_5, of.odds AS o_fin
        FROM odds_trifecta o5
        JOIN odds_trifecta of_
          ON o5.race_id = of_.race_id
         AND o5.combination = of_.combination
        JOIN races r ON r.race_id = o5.race_id
        WHERE o5.snapshot_label = 'T-5min'
          AND of_.snapshot_label = 'T-1min'
    """
    # SQLite/Postgres どちらも of は予約語なので別名 of_ にしている
    # 結果のカラム名のためにエイリアス調整
    sql = """
        SELECT o5.race_id, o5.combination,
               o5.odds AS odds_5,
               of_.odds AS odds_fin
        FROM odds_trifecta o5
        JOIN odds_trifecta of_
          ON o5.race_id = of_.race_id
         AND o5.combination = of_.combination
        WHERE o5.snapshot_label = 'T-5min'
          AND of_.snapshot_label = 'T-1min'
    """
    cur = conn.execute(sql)
    rows = cur.fetchall()

    if not rows:
        print("⚠️ T-5min と final の両スナップショットを持つレースが無し")
        return

    print(f"--- ペア集計対象: {len(rows):,} 組 (race × 買い目) ---\n")

    diffs = []
    rel_diffs = []
    by_race_min_5 = defaultdict(lambda: (float("inf"), None))
    by_race_min_fin = defaultdict(lambda: (float("inf"), None))

    for race_id, combo, o5, ofin in rows:
        diff = ofin - o5
        rel = (ofin - o5) / o5 if o5 else 0
        diffs.append(diff)
        rel_diffs.append(rel)
        if o5 < by_race_min_5[race_id][0]:
            by_race_min_5[race_id] = (o5, combo)
        if ofin < by_race_min_fin[race_id][0]:
            by_race_min_fin[race_id] = (ofin, combo)

    diffs.sort()
    rel_diffs.sort()
    n = len(diffs)

    def pct(seq, q):
        return seq[int(n * q)]

    print("--- 全買い目: オッズ差 (final − T-5) ---")
    print(f"  平均差:   {sum(diffs)/n:+.2f}")
    print(f"  中央値:   {pct(diffs, 0.5):+.2f}")
    print(f"  10%分位:  {pct(diffs, 0.1):+.2f}")
    print(f"  25%分位:  {pct(diffs, 0.25):+.2f}")
    print(f"  75%分位:  {pct(diffs, 0.75):+.2f}")
    print(f"  90%分位:  {pct(diffs, 0.9):+.2f}")
    print()

    print("--- 全買い目: 相対差 (final/T-5 − 1) ---")
    print(f"  平均:    {sum(rel_diffs)/n*100:+.1f}%")
    print(f"  中央値:  {pct(rel_diffs, 0.5)*100:+.1f}%")
    print(f"  10%分位: {pct(rel_diffs, 0.1)*100:+.1f}%")
    print(f"  90%分位: {pct(rel_diffs, 0.9)*100:+.1f}%")
    print()

    # 本命の変化
    common = set(by_race_min_5) & set(by_race_min_fin)
    same = 0
    diff_combo = 0
    fav_diffs = []
    fav_rel = []
    l4_5 = 0
    l4_fin = 0
    l4_both = 0
    l4_5_only = 0
    l4_fin_only = 0
    for rid in common:
        o5, c5 = by_race_min_5[rid]
        of, cf = by_race_min_fin[rid]
        if c5 == cf:
            same += 1
        else:
            diff_combo += 1
        fav_diffs.append((of - o5) * 100)
        if o5:
            fav_rel.append((of - o5) / o5)

        p5 = o5 * 100
        pf = of * 100
        is5 = 500 <= p5 <= 1000
        isf = 500 <= pf <= 1000
        if is5: l4_5 += 1
        if isf: l4_fin += 1
        if is5 and isf: l4_both += 1
        elif is5: l4_5_only += 1
        elif isf: l4_fin_only += 1

    print(f"--- 本命 (1番人気買い目) の動き ---")
    print(f"  対象レース: {len(common):,}")
    print(f"  本命一致:        {same:,} ({same/len(common)*100:.1f}%)")
    print(f"  本命入れ替わり:  {diff_combo:,} ({diff_combo/len(common)*100:.1f}%)")
    print()
    fav_diffs.sort()
    fav_rel.sort()
    n2 = len(fav_diffs)
    print("--- 本命オッズの動き (払戻円換算) ---")
    print(f"  平均差:    {sum(fav_diffs)/n2:+.1f} 円")
    print(f"  中央値:    {fav_diffs[n2//2]:+.1f} 円")
    print(f"  10%分位:   {fav_diffs[int(n2*0.1)]:+.1f} 円")
    print(f"  90%分位:   {fav_diffs[int(n2*0.9)]:+.1f} 円")
    print(f"  相対差中央値: {fav_rel[n2//2]*100:+.2f}%")
    print(f"  相対差平均:   {sum(fav_rel)/n2*100:+.2f}%")
    print()

    # L4 判定の差
    print("--- L4 判定 (本命 500〜1000円) の T-5 → final 維持率 ---")
    print(f"  T-5min  で L4: {l4_5:,} レース")
    print(f"  final   で L4: {l4_fin:,} レース")
    print(f"  両方で L4:     {l4_both:,} レース")
    print(f"  T-5のみ L4:    {l4_5_only:,} レース (final で範囲外)")
    print(f"  finalのみ L4:  {l4_fin_only:,} レース (T-5 で範囲外)")
    if l4_5:
        print()
        print(f"→ T-5min で L4 判定 → 締切時 final でも L4 を維持: {l4_both/l4_5*100:.1f}%")
        print(f"   (12% は本命オッズが 500 円割る or 1000 円超で範囲外に)")
    print()

    # 締切ギリギリに動くものを別角度で
    big_movers_500 = sum(1 for d in fav_diffs if abs(d) > 500)
    big_movers_200 = sum(1 for d in fav_diffs if abs(d) > 200)
    big_movers_100 = sum(1 for d in fav_diffs if abs(d) > 100)
    print("--- 本命の「大きく動いた」レース割合 ---")
    print(f"  |差| > 100円: {big_movers_100}/{n2} ({big_movers_100/n2*100:.1f}%)")
    print(f"  |差| > 200円: {big_movers_200}/{n2} ({big_movers_200/n2*100:.1f}%)")
    print(f"  |差| > 500円: {big_movers_500}/{n2} ({big_movers_500/n2*100:.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()
