"""
残りのシグナル検証

検証1: オッズが割れるレース (1番人気払戻金額別)
検証2: 競艇場固定戦略 (Motor 35-50% フィルタ後)
検証3: Ultra Signal (艇5+tilt=3.0+A2) の月別詳細
検証4: Ultra Signal の三連単配当分布
検証5: 展示ST × 周回タイム の不整合パターン
"""
import sqlite3
import random
import statistics
from typing import List

DB = "data/boatrace.db"
N_BOOT = 2000
random.seed(42)


def bootstrap_ci(payouts: List[float], bet: float = 100) -> dict:
    n = len(payouts)
    if n == 0:
        return {"n": 0, "roi": None, "lo": None, "hi": None, "p0": None}
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rois.append(sum(sample) / n / bet - 1.0)
    rois.sort()
    return {
        "n": n,
        "roi": sum(payouts) / n / bet - 1.0,
        "lo": rois[int(N_BOOT * 0.025)],
        "hi": rois[int(N_BOOT * 0.975)],
        "p0": sum(1 for r in rois if r > 0) / N_BOOT,
    }


def show(label: str, r: dict):
    if r["n"] == 0:
        print(f"  {label:<55} [no data]")
        return
    flag = ""
    if r["hi"] > 0: flag = " *** CI+"
    elif r["p0"] > 0.05: flag = " * P>5%"
    print(f"  {label:<55} n={r['n']:>7,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]  P>0={r['p0']:>5.1%}{flag}")


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: オッズが割れるレース (三連単1番人気の払戻金額 = 人気の集中度)
    # =========================================================
    print("=" * 110)
    print("[Test 1] 三連単1番人気の払戻 (= 人気集中度) 別 1号艇単勝 ROI")
    print("=" * 110)
    print("(低=ガチガチ本命、高=人気バラける = オッズが割れる)")
    print()
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts
            WHERE bet_type='trifecta'
            GROUP BY race_id
        )
        SELECT
            CASE
                WHEN mt.min_p < 500 THEN 'A: <500 (超本命)'
                WHEN mt.min_p < 1000 THEN 'B: 500-1k (本命)'
                WHEN mt.min_p < 2000 THEN 'C: 1k-2k (やや本命)'
                WHEN mt.min_p < 5000 THEN 'D: 2k-5k (拮抗)'
                WHEN mt.min_p < 10000 THEN 'E: 5k-10k (荒れ寄り)'
                ELSE 'F: 10k+ (波乱)'
            END as tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        GROUP BY tier
        ORDER BY MIN(mt.min_p)
    """)
    print(f"{'tier':<28} {'n':>10} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 75)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0) / 100 - 1
        marker = " <<<" if abs(roi) < 0.07 else ""
        print(f"  {tier:<26} {n:>10,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    # Bootstrap CI for "本命" range (where market is confident)
    print()
    print("Bootstrap CI (本命系):")
    for label, lo, hi in [
        ("超本命 (<500)", 0, 500),
        ("本命 (500-1k)", 500, 1000),
        ("拮抗 (2k-5k)", 2000, 5000),
        ("波乱 (10k+)", 10000, 99999999),
    ]:
        cur = conn.execute("""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            )
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE mt.min_p >= ? AND mt.min_p < ?
        """, (lo, hi))
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(label, r)

    # =========================================================
    # 検証2: 会場別 Motor 35-50% フィルタ ROI
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 2] 会場別 1号艇 Sweet Spot (Motor 35-50% フィルタ) ROI")
    print("=" * 110)
    cur = conn.execute("""
        SELECT r.stadium_number, s.name,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
        FROM races r
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
        GROUP BY r.stadium_number
        HAVING n >= 1000
        ORDER BY (ap - 100) DESC
    """)
    results = cur.fetchall()
    print(f"{'順位':<6} {'場':<10} {'n':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 60)
    for i, (sid, name, n, wr, ap) in enumerate(results, 1):
        roi = (ap or 0) / 100 - 1
        marker = " <<<" if i <= 3 else ""
        print(f"  {i:>2}    {name:<8} {n:>8,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    # Top5 会場で Bootstrap CI
    print()
    print("Top5 会場の Bootstrap CI:")
    for sid, name, _, _, _ in results[:5]:
        cur = conn.execute("""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE r.stadium_number = ?
              AND e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
        """, (sid,))
        payouts = [float(r[0]) for r in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(f"{name} (Motor35-50)", r)

    # =========================================================
    # 検証3: Ultra Signal の月別詳細
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 3] Ultra Signal (艇5+tilt=3.0+A2) 月別分布")
    print("=" * 110)
    cur = conn.execute("""
        SELECT substr(r.race_date, 1, 7) as ym,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap_win,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp_tri.payout, 0) ELSE 0 END) as ap_tri
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 5
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='5'
        LEFT JOIN race_payouts pp_tri ON pp_tri.race_id = r.race_id AND pp_tri.bet_type='trifecta'
        WHERE p.tilt_adjustment = 3.0
        GROUP BY ym
        ORDER BY ym
    """)
    print(f"{'月':<10} {'n':>4} {'win率':>8} {'単勝avg':>10} {'三連単avg':>12}")
    print("-" * 55)
    total_n = 0
    for ym, n, wr, ap_w, ap_t in cur.fetchall():
        print(f"  {ym:<8} {n:>4} {wr:>8.3f} {ap_w or 0:>10.1f} {ap_t or 0:>12.1f}")
        total_n += n
    print(f"\n  合計 n = {total_n}")

    # =========================================================
    # 検証4: Ultra Signal の三連単配当分布
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 4] Ultra Signal 的中時の三連単配当分布")
    print("=" * 110)
    cur = conn.execute("""
        SELECT pp_tri.combination, pp_tri.payout, r.race_id, r.race_date, s.name as stadium
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 5
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        LEFT JOIN race_payouts pp_tri ON pp_tri.race_id = r.race_id AND pp_tri.bet_type='trifecta'
        WHERE p.tilt_adjustment = 3.0 AND res.finishing_position = 1
        ORDER BY pp_tri.payout
    """)
    rows = [(c, p, rid, rd, st) for c, p, rid, rd, st in cur.fetchall() if c and p]
    if rows:
        payouts = [p for _, p, _, _, _ in rows]
        print(f"  的中レース数: {len(rows)}")
        print(f"  最小: {min(payouts):,}円  中央: {int(statistics.median(payouts)):,}円  "
              f"平均: {int(statistics.mean(payouts)):,}円  最大: {max(payouts):,}円")
        print()
        print("  全的中レース:")
        for combo, payout, rid, rd, st in rows:
            print(f"    {rd} {st:<6} {combo}: ¥{payout:>7,}")

        # 10通り絞り買い (上位5艇から X-Y選択) ROI 推定
        # 1着=5, X,Y は他5艇から → 5P2 = 20通り
        # 10通り絞ると配当はそのまま、コストは半分
        avg_payout_when_hit = statistics.mean(payouts)
        # 該当レース全体数
        cur = conn.execute("""
            SELECT COUNT(*) FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
            JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
            WHERE p.tilt_adjustment = 3.0
        """)
        n_total = cur.fetchone()[0]
        win_rate = len(rows) / n_total
        # 全20点買い: cost = 20*100 = 2000円
        # ROI_20 = (win_rate * avg_payout) / 2000 - 1
        # 但し10通り絞り買いで的中率 75% 維持 (上位10通りに勝ち目組合せが含まれる確率)
        roi_20 = win_rate * avg_payout_when_hit / (20 * 100) - 1
        # 10通り絞りで的中率が 14/15 = 93% で残る場合
        # (実際は backtest で 14/15 当たっていた)
        retained_hit = 14/15 if len(rows) >= 14 else 0.9
        roi_10_focused = (win_rate * retained_hit) * avg_payout_when_hit / (10 * 100) - 1
        print()
        print(f"  全20通り買い ROI 概算: {roi_20:+.2%}")
        print(f"  10通り絞り買い ROI 概算 (絞込効率 93%): {roi_10_focused:+.2%}")

    # =========================================================
    # 検証5: 展示ST × 周回タイム 不整合
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 5] 1号艇 展示ST順位 × 周回タイム順位 別 ROI (再検証)")
    print("=" * 110)
    cur = conn.execute("""
        WITH ex_ranks AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.start_timing_exhibition ASC) as st_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL AND p.start_timing_exhibition IS NOT NULL
        )
        SELECT er.ex_rk, er.st_rk,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
        FROM ex_ranks er
        JOIN race_results res ON er.race_id = res.race_id AND er.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = er.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE er.boat_number = 1
        GROUP BY er.ex_rk, er.st_rk
        HAVING n >= 200
        ORDER BY er.ex_rk, er.st_rk
    """)
    print(f"{'ex_rk':<8} {'st_rk':<8} {'n':>8} {'1着率':>10} {'ROI':>10}")
    print("-" * 50)
    print("(ex_rk=展示タイム順位, st_rk=展示ST順位, ともに 1=最良)")
    for ex_rk, st_rk, n, wr, ap in cur.fetchall():
        roi = (ap or 0) / 100 - 1
        if ex_rk <= 2 and st_rk <= 2 and roi > -0.10:
            marker = " <<< 好行き足"
        elif ex_rk == 1 and st_rk >= 4:
            marker = " <<< ST遅・展示速"
        elif ex_rk >= 4 and st_rk == 1:
            marker = " <<< ST速・展示遅 (罠)"
        else:
            marker = ""
        print(f"  {ex_rk:<6} {st_rk:<6} {n:>8,} {wr:>10.3f} {roi:>+10.2%}{marker}")

    conn.close()


if __name__ == "__main__":
    main()
