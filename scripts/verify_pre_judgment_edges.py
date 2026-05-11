"""
事前判定可能エッジの探索

Priority 1: 進入コース変更 (枠なり崩れ)
  - 1号艇が進入コース 1 でないレース
  - 直前情報 (course_number) で判定可能

Priority 2: 節順 (series_day) の影響
  - series_day が未投入なので、race_id から推定
  - 同会場の連続開催日を識別

Priority 3: グレード × 級別の交互作用
  - race_grade_number と 1号艇 class_number の組合せ
  - SG/G1 vs 一般戦での効率差
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
    if r["lo"] > 0: flag = " *** P>0=100%"
    elif r["hi"] > 0: flag = " ** CI+"
    elif r["p0"] > 0.05: flag = " * P>5%"
    print(f"  {label:<55} n={r['n']:>7,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]  P>0={r['p0']:>5.1%}{flag}")


def fetch(conn, where: str) -> List[float]:
    cur = conn.execute(f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE {where}
    """)
    return [float(row[0]) for row in cur.fetchall()]


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # Priority 1: 進入コース変更レース
    # =========================================================
    print("=" * 110)
    print("[Priority 1] 1号艇進入コース別 単勝 ROI (1号艇枠なり崩れ検証)")
    print("=" * 110)
    print()
    print("(全データ: 2022-2026)")
    for course in [1, 2, 3, 4, 5, 6]:
        payouts = fetch(conn, f"p.course_number = {course}")
        r = bootstrap_ci(payouts)
        show(f"1号艇 進入C{course} (枠なり={'YES' if course==1 else 'NO'})", r)

    # 2026年のみ
    print()
    print("(2026年のみ)")
    for course in [1, 2, 3, 4, 5, 6]:
        payouts = fetch(conn, f"p.course_number = {course} AND r.race_date >= '2026-01-01'")
        r = bootstrap_ci(payouts)
        show(f"1号艇 進入C{course} (2026)", r)

    # 進入コース×三連単1番人気帯 (組合せ検証)
    print()
    print("[Priority 1-b] 進入崩れ + 三連単1番人気帯")
    for course in [2, 3]:
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            )
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE p.course_number = {course} AND r.race_date >= '2026-01-01'
        """)
        for label, lo, hi in [
            ("超本命 <500", 0, 500),
            ("本命 500-1k", 500, 1000),
            ("やや本命 1k-2k", 1000, 2000),
            ("拮抗 2k-5k", 2000, 5000),
        ]:
            cur = conn.execute(f"""
                WITH min_tri AS (
                    SELECT race_id, MIN(payout) as min_p
                    FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
                )
                SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
                FROM races r
                JOIN min_tri mt ON r.race_id = mt.race_id
                JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
                JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
                LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
                WHERE p.course_number = {course} AND r.race_date >= '2026-01-01'
                  AND mt.min_p >= {lo} AND mt.min_p < {hi}
            """)
            payouts = [float(row[0]) for row in cur.fetchall()]
            r = bootstrap_ci(payouts)
            show(f"進入C{course} + {label}", r)

    # =========================================================
    # Priority 2: 節順 (連続開催日の影響)
    # =========================================================
    print()
    print("=" * 110)
    print("[Priority 2] 節順 (連続開催何日目) 別 ROI")
    print("=" * 110)
    print()
    print("(注: series_day が未投入のため、同会場連続日数を推定)")
    print()
    cur = conn.execute("""
        WITH consecutive_days AS (
            SELECT r1.race_id, r1.stadium_number,
                   (SELECT COUNT(DISTINCT r2.race_date)
                    FROM races r2
                    WHERE r2.stadium_number = r1.stadium_number
                      AND r2.race_date <= r1.race_date
                      AND r2.race_date > date(r1.race_date, '-7 days')) as day_in_series
            FROM races r1
        )
        SELECT cd.day_in_series,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
        FROM consecutive_days cd
        JOIN races r ON cd.race_id = r.race_id
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-01-01'
        GROUP BY cd.day_in_series
        ORDER BY cd.day_in_series
    """)
    print(f"{'節何日目':<10} {'n':>10} {'1着率':>10} {'avg配当':>10} {'ROI':>10}")
    for d, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        marker = " <<<" if abs(roi) < 0.07 else ""
        print(f"  {d}日目     {n:>10,} {wr or 0:>10.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    # 節後半のみ + 三連単本命帯 (組合せ)
    print()
    print("[Priority 2-b] 節後半 (5日目以降) + 三連単1番人気帯")
    for label, lo, hi in [
        ("本命 500-1k", 500, 1000),
        ("やや本命 1k-2k", 1000, 2000),
    ]:
        cur = conn.execute(f"""
            WITH consecutive_days AS (
                SELECT r1.race_id, r1.stadium_number,
                       (SELECT COUNT(DISTINCT r2.race_date)
                        FROM races r2
                        WHERE r2.stadium_number = r1.stadium_number
                          AND r2.race_date <= r1.race_date
                          AND r2.race_date > date(r1.race_date, '-7 days')) as day_in_series
                FROM races r1
            ),
            min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            )
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM consecutive_days cd
            JOIN races r ON cd.race_id = r.race_id
            JOIN min_tri mt ON r.race_id = mt.race_id
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE r.race_date >= '2026-01-01'
              AND cd.day_in_series >= 5
              AND mt.min_p >= {lo} AND mt.min_p < {hi}
        """)
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(f"節5日目以降 + {label}", r)

    # =========================================================
    # Priority 3: グレード × 級別の交互作用
    # =========================================================
    print()
    print("=" * 110)
    print("[Priority 3] グレード × 1号艇級別 別 ROI")
    print("=" * 110)
    grade_names = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}
    class_names = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}

    print()
    print("(2026年データ)")
    print(f"{'グレード':<10} {'class':<8} {'n':>8} {'1着率':>10} {'avg配当':>10} {'ROI':>10}")
    cur = conn.execute("""
        SELECT r.race_grade_number, e.class_number,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-01-01' AND r.race_grade_number IS NOT NULL AND e.class_number IS NOT NULL
        GROUP BY r.race_grade_number, e.class_number
        HAVING n >= 100
        ORDER BY r.race_grade_number, e.class_number
    """)
    for grade, cls, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        marker = " <<<" if abs(roi) < 0.07 else ""
        print(f"  {grade_names.get(grade, '?'):<8} {class_names.get(cls, '?'):<6} "
              f"{n:>8,} {wr or 0:>10.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    # 注目: SG/G1 で A1 1号艇 + 三連単本命帯
    print()
    print("[Priority 3-b] SG/G1 + A1 1号艇 + 三連単本命帯 組合せ")
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-01-01'
          AND r.race_grade_number IN (1, 2)
          AND e.class_number = 1
          AND mt.min_p >= 500 AND mt.min_p < 1000
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("SG/G1 + A1 1号艇 + 三連単本命500-1k", r)

    # 一般戦 + B1 1号艇 + 三連単本命帯
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-01-01'
          AND r.race_grade_number = 5
          AND e.class_number = 3
          AND mt.min_p >= 500 AND mt.min_p < 1000
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("一般戦 + B1 1号艇 + 三連単本命500-1k", r)

    # 一般戦 + A1 1号艇 + 三連単本命帯
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-01-01'
          AND r.race_grade_number = 5
          AND e.class_number = 1
          AND mt.min_p >= 500 AND mt.min_p < 1000
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("一般戦 + A1 1号艇 + 三連単本命500-1k", r)

    conn.close()


if __name__ == "__main__":
    main()
