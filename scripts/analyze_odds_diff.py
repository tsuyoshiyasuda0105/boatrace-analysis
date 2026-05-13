"""T-15min と T-5min のオッズ差分析

同じレース・同じ買い目で T-15min と T-5min のオッズがどう動いたか集計。
本命 (最小オッズ買い目) の動き、L4 判定の安定性などを出す。

実行:
  .venv\\Scripts\\python.exe scripts\\analyze_odds_diff.py [--days 30]
"""
from __future__ import annotations

import argparse
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
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30, help="過去 N 日分を分析")
    args = p.parse_args()

    conn = connect()

    # T-15min と T-5min の両方を持つレース・買い目を JOIN
    print(f"=== T-15min vs T-5min オッズ差分析 (過去 {args.days} 日) ===\n")

    # まずスナップショット別件数
    cur = conn.execute(
        f"""
        SELECT snapshot_label, COUNT(*) AS n
        FROM odds_trifecta o
        JOIN races r ON r.race_id = o.race_id
        WHERE r.race_date >= (CURRENT_DATE - INTERVAL '{args.days} days')::text
        GROUP BY snapshot_label
        ORDER BY snapshot_label
        """
        if hasattr(conn, "_kind") and conn._kind == "postgres"
        else f"""
        SELECT snapshot_label, COUNT(*) AS n
        FROM odds_trifecta o
        JOIN races r ON r.race_id = o.race_id
        WHERE r.race_date >= date('now', '-{args.days} days')
        GROUP BY snapshot_label
        ORDER BY snapshot_label
        """
    )
    print("--- スナップショット別件数 ---")
    for lab, n in cur.fetchall():
        print(f"  {lab or '(NULL)':<12} : {n:>10,} 行")
    print()

    # 同一 race_id × combination で両方持つもの抽出
    cur = conn.execute(
        f"""
        SELECT o15.race_id, o15.combination, o15.odds AS odds_15, o5.odds AS odds_5
        FROM odds_trifecta o15
        JOIN odds_trifecta o5
          ON o15.race_id = o5.race_id
         AND o15.combination = o5.combination
        JOIN races r ON r.race_id = o15.race_id
        WHERE o15.snapshot_label = 'T-15min'
          AND o5.snapshot_label  = 'T-5min'
          AND r.race_date >= (CURRENT_DATE - INTERVAL '{args.days} days')::text
        """
        if hasattr(conn, "_kind") and conn._kind == "postgres"
        else f"""
        SELECT o15.race_id, o15.combination, o15.odds AS odds_15, o5.odds AS odds_5
        FROM odds_trifecta o15
        JOIN odds_trifecta o5
          ON o15.race_id = o5.race_id
         AND o15.combination = o5.combination
        JOIN races r ON r.race_id = o15.race_id
        WHERE o15.snapshot_label = 'T-15min'
          AND o5.snapshot_label  = 'T-5min'
          AND r.race_date >= date('now', '-{args.days} days')
        """
    )
    rows = cur.fetchall()

    if not rows:
        print("⚠️ T-15min と T-5min の両スナップショットを持つレースが見つかりません")
        print("   → odds_trifecta に snapshot_label が記録されていない可能性")
        return

    print(f"--- ペア集計対象: {len(rows):,} 組 (race × 買い目) ---\n")

    # 全買い目の差分布
    diffs = []
    rel_diffs = []
    by_race_min_odds_15 = defaultdict(lambda: (float("inf"), None))  # race_id -> (min_odds, combination)
    by_race_min_odds_5 = defaultdict(lambda: (float("inf"), None))

    for race_id, combo, o15, o5 in rows:
        diff = o5 - o15
        rel = (o5 - o15) / o15 if o15 else 0
        diffs.append(diff)
        rel_diffs.append(rel)
        if o15 < by_race_min_odds_15[race_id][0]:
            by_race_min_odds_15[race_id] = (o15, combo)
        if o5 < by_race_min_odds_5[race_id][0]:
            by_race_min_odds_5[race_id] = (o5, combo)

    diffs.sort()
    rel_diffs.sort()
    n = len(diffs)

    def pct(seq, q):
        return seq[int(n * q)]

    print("--- 全買い目のオッズ差 (T-5 − T-15) ---")
    print(f"  サンプル: {n:,}")
    print(f"  平均差:   {sum(diffs)/n:+.2f}")
    print(f"  中央値:   {pct(diffs, 0.5):+.2f}")
    print(f"  10%分位:  {pct(diffs, 0.1):+.2f}")
    print(f"  90%分位:  {pct(diffs, 0.9):+.2f}")
    print()
    print("--- 全買い目の相対差 (T-5 / T-15 − 1) ---")
    print(f"  平均:    {sum(rel_diffs)/n*100:+.1f}%")
    print(f"  中央値:  {pct(rel_diffs, 0.5)*100:+.1f}%")
    print(f"  10%分位: {pct(rel_diffs, 0.1)*100:+.1f}%")
    print(f"  90%分位: {pct(rel_diffs, 0.9)*100:+.1f}%")
    print()

    # 本命 (最小オッズ) の変化
    print("--- 本命 (最小オッズ買い目) の動き ---")
    common_races = set(by_race_min_odds_15) & set(by_race_min_odds_5)
    print(f"  対象レース: {len(common_races):,}")

    same_combo = 0
    diff_combo = 0
    fav_diffs = []
    fav_rel_diffs = []
    l4_15 = 0
    l4_5 = 0
    l4_both = 0
    l4_15_only = 0
    l4_5_only = 0
    for race_id in common_races:
        o15, c15 = by_race_min_odds_15[race_id]
        o5, c5 = by_race_min_odds_5[race_id]
        if c15 == c5:
            same_combo += 1
        else:
            diff_combo += 1
        fav_diffs.append(o5 * 100 - o15 * 100)  # 100円換算
        if o15:
            fav_rel_diffs.append((o5 - o15) / o15)

        # L4 判定: 本命の払戻 500〜1000円
        payout_15 = o15 * 100
        payout_5 = o5 * 100
        is_l4_15 = 500 <= payout_15 <= 1000
        is_l4_5 = 500 <= payout_5 <= 1000
        if is_l4_15:
            l4_15 += 1
        if is_l4_5:
            l4_5 += 1
        if is_l4_15 and is_l4_5:
            l4_both += 1
        elif is_l4_15 and not is_l4_5:
            l4_15_only += 1
        elif is_l4_5 and not is_l4_15:
            l4_5_only += 1

    print(f"  T-15 と T-5 で本命が一致:     {same_combo:,} ({same_combo/len(common_races)*100:.1f}%)")
    print(f"  本命が入れ替わった:           {diff_combo:,} ({diff_combo/len(common_races)*100:.1f}%)")
    print()

    fav_diffs.sort()
    fav_rel_diffs.sort()
    n2 = len(fav_diffs)
    print("--- 本命オッズの動き (払戻円換算) ---")
    print(f"  平均差:    {sum(fav_diffs)/n2:+.1f} 円")
    print(f"  中央値:    {fav_diffs[n2//2]:+.1f} 円")
    print(f"  10%分位:   {fav_diffs[int(n2*0.1)]:+.1f} 円")
    print(f"  90%分位:   {fav_diffs[int(n2*0.9)]:+.1f} 円")
    print()
    print(f"  相対差中央値: {fav_rel_diffs[n2//2]*100:+.2f}%")
    print(f"  相対差平均:   {sum(fav_rel_diffs)/n2*100:+.2f}%")
    print()

    # L4 判定の差
    print("--- L4 判定 (本命 500〜1000円) の安定性 ---")
    print(f"  T-15min で L4: {l4_15:,} レース")
    print(f"  T-5min  で L4: {l4_5:,} レース")
    print(f"  両方で L4:     {l4_both:,} レース ({l4_both/max(1,l4_15)*100:.1f}% of T-15)")
    print(f"  T-15のみで L4: {l4_15_only:,} レース (T-5 で範囲外)")
    print(f"  T-5のみで L4:  {l4_5_only:,} レース (T-15 で範囲外)")
    print()
    print("→ T-15 を基準にした L4 判定の予測信頼度:")
    if l4_15:
        print(f"   {l4_both/l4_15*100:.1f}% は T-5 でも L4 判定を維持")
        print(f"   {l4_15_only/l4_15*100:.1f}% は T-5 で範囲外 (オッズ動きで外れた)")

    conn.close()


if __name__ == "__main__":
    main()
