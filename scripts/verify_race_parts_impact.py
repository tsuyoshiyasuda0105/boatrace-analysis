"""
race_parts と 1着率/ROI の関係検証

専門家知見:
  - シリンダー/クランクシャフト 交換 = モーター極悪状態 → 即消し
  - ピストン/リング 全交換 = 要注意
  - 但し、整備成功 (展示タイム改善) なら評価据置

検証:
  1. 部品交換種類別の各艇 1着率
  2. 部品交換ありなしの ROI 比較
  3. shaft 交換時の艇別 ROI (n は小さいかも)
"""
import sqlite3
import random
import statistics
from typing import List

DB = "data/boatrace.db"
N_BOOT = 1000
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
        print(f"  {label:<45} [no data]")
        return
    print(f"  {label:<45} n={r['n']:>4,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]")


def main():
    conn = sqlite3.connect(DB)

    print("=" * 90)
    print("[Test 1] 部品交換種類別 各艇 1着率")
    print("=" * 90)
    cur = conn.execute("""
        SELECT rp.part_code, rp.boat_number, COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM race_parts rp
        JOIN race_results res ON rp.race_id = res.race_id AND rp.boat_number = res.boat_number
        GROUP BY rp.part_code, rp.boat_number
        HAVING n >= 5
        ORDER BY rp.part_code, rp.boat_number
    """)
    print(f"{'部品':<12} {'艇':<6} {'n':>4} {'1着率':>8}")
    for part, boat, n, wr in cur.fetchall():
        print(f"  {part:<10} {boat:<4} {n:>4} {wr:>8.3f}")

    print()
    print("=" * 90)
    print("[Test 2] 1号艇 部品交換あり/なし の単勝 ROI")
    print("=" * 90)
    # 1号艇に何らかの部品交換があったレース
    cur = conn.execute("""
        SELECT DISTINCT race_id FROM race_parts WHERE boat_number = 1
    """)
    parts_race_ids = [r[0] for r in cur.fetchall()]
    n_parts = len(parts_race_ids)
    print(f"1号艇に部品交換ありのレース: {n_parts}")

    # ROI 比較 (該当部品交換 vs なし)
    if parts_race_ids:
        placeholders = ",".join(["?"] * len(parts_race_ids))
        # 部品交換あり
        cur = conn.execute(f"""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE r.race_id IN ({placeholders})
        """, parts_race_ids)
        payouts = [float(r[0]) for r in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show("1号艇 部品交換あり 単勝", r)

        # 部品交換なし (同期間)
        cur = conn.execute(f"""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE r.race_id NOT IN ({placeholders})
              AND r.race_date >= '2026-05-05' AND r.race_date <= '2026-05-12'
        """, parts_race_ids)
        payouts = [float(r[0]) for r in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show("1号艇 部品交換なし 単勝 (同期間)", r)

    print()
    print("=" * 90)
    print("[Test 3] 部品交換数別 1着率 (1号艇)")
    print("=" * 90)
    cur = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM race_parts WHERE race_id = r.race_id AND boat_number = 1) as n_parts,
            COUNT(*) as n_races,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
        FROM races r
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2026-05-05' AND r.race_date <= '2026-05-12'
        GROUP BY n_parts
        ORDER BY n_parts
    """)
    print(f"{'部品交換数':<10} {'n_races':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    for n_p, n_r, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {n_p:<8} {n_r:>8,} {wr or 0:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    print()
    print("=" * 90)
    print("[Test 4] shaft (クランクシャフト) 交換時の艇別")
    print("=" * 90)
    cur = conn.execute("""
        SELECT rp.race_id, rp.boat_number, res.finishing_position
        FROM race_parts rp
        JOIN race_results res ON rp.race_id = res.race_id AND rp.boat_number = res.boat_number
        WHERE rp.part_code = 'shaft'
    """)
    for rid, bn, pos in cur.fetchall():
        print(f"  {rid} 艇{bn}: {pos}着")

    conn.close()


if __name__ == "__main__":
    main()
