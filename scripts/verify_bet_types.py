"""
Priority B: 券種別 +EV の網羅検証

目的: 1号艇単勝以外でも +EV な戦略があるか確認

検証条件: 三連単1番人気500-1000円帯のレース (2026年データ)

検証する券種:
  - 単勝 1
  - 複勝 1
  - 2連単 1-2, 1-3, 1-4, 1-5, 1-6
  - 2連複 1=2, 1=3, 1=4, 1=5, 1=6
  - ワイド 1-2, 1-3, 1-4, 1-5, 1-6
  - 3連複 1-2-3, 1-2-4, ..., 1-X-Y 上位
"""
import sqlite3
import random
import statistics
from typing import List

DB = "data/boatrace.db"
N_BOOT = 2000
random.seed(42)

# 2026年データ
YEAR_FILTER = "r.race_date >= '2026-01-01'"
# 三連単1番人気が500-1000円帯のレース (最強の +EV ゾーン)
FAVORITE_BAND = "mt.min_p >= 500 AND mt.min_p < 1000"


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
        print(f"  {label:<50} [no data]")
        return
    flag = ""
    if r["lo"] > 0: flag = " *** P>0=100%"
    elif r["hi"] > 0: flag = " ** CI+"
    elif r["p0"] > 0.05: flag = " * P>5%"
    print(f"  {label:<50} n={r['n']:>5,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]  P>0={r['p0']:>5.1%}{flag}")


def fetch_payouts_for_bet(conn, bet_type: str, combo: str, hit_condition: str) -> List[float]:
    """指定券種・買い目について、的中条件を満たした時の払戻金額を取得"""
    cur = conn.execute(f"""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN {hit_condition} THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_results res ON r.race_id = res.race_id
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type = '{bet_type}' AND pp.combination = '{combo}'
        WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
          AND res.boat_number = 1
    """)
    return [float(row[0]) for row in cur.fetchall()]


def main():
    conn = sqlite3.connect(DB)

    print("=" * 100)
    print("Priority B: 券種別 +EV 検証")
    print("条件: 2026年 + 三連単1番人気500-1000円帯のレース")
    print("=" * 100)

    # =========================================================
    # 検証1: 単勝・複勝 (1号艇)
    # =========================================================
    print("\n[Test 1] 1号艇 単勝 vs 複勝")
    cur = conn.execute(f"""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
    """)
    p = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(p)
    show("1号艇 単勝", r)

    cur = conn.execute(f"""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT CASE WHEN res.finishing_position<=2 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='place' AND pp.combination='1'
        WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
    """)
    p = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(p)
    show("1号艇 複勝", r)

    # =========================================================
    # 検証2: 2連単 1-X (1号艇1着 + X号艇2着)
    # =========================================================
    print("\n[Test 2] 2連単 1-X")
    for x in [2, 3, 4, 5, 6]:
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            ),
            r1 AS (
                SELECT race_id FROM race_results WHERE boat_number=1 AND finishing_position=1
            ),
            r2 AS (
                SELECT race_id FROM race_results WHERE boat_number={x} AND finishing_position=2
            )
            SELECT CASE WHEN r1.race_id IS NOT NULL AND r2.race_id IS NOT NULL
                        THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            LEFT JOIN r1 ON r.race_id = r1.race_id
            LEFT JOIN r2 ON r.race_id = r2.race_id
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='exacta' AND pp.combination='1-{x}'
            WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
        """)
        p = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(p)
        show(f"2連単 1-{x}", r)

    # =========================================================
    # 検証3: 2連複 1=X
    # =========================================================
    print("\n[Test 3] 2連複 1=X (順不同)")
    for x in [2, 3, 4, 5, 6]:
        combo = f"1={x}"
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            ),
            top2 AS (
                SELECT race_id, GROUP_CONCAT(boat_number) as boats
                FROM race_results
                WHERE finishing_position <= 2
                GROUP BY race_id
            )
            SELECT CASE
                WHEN top2.boats LIKE '%1%' AND top2.boats LIKE '%{x}%'
                THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            LEFT JOIN top2 ON r.race_id = top2.race_id
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='quinella' AND pp.combination='{combo}'
            WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
        """)
        p = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(p)
        show(f"2連複 1={x}", r)

    # =========================================================
    # 検証4: ワイド 1-X (1号艇と X号艇が共に3着以内)
    # =========================================================
    print("\n[Test 4] ワイド 1-X (両方3着以内)")
    for x in [2, 3, 4, 5, 6]:
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            ),
            top3 AS (
                SELECT race_id, GROUP_CONCAT(boat_number) as boats
                FROM race_results
                WHERE finishing_position <= 3
                GROUP BY race_id
            )
            SELECT CASE
                WHEN top3.boats LIKE '%1%' AND top3.boats LIKE '%{x}%'
                THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            LEFT JOIN top3 ON r.race_id = top3.race_id
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='quinella_place' AND pp.combination='1={x}'
            WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
        """)
        p = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(p)
        show(f"ワイド 1-{x}", r)

    # =========================================================
    # 検証5: 3連複 1-X-Y (1号艇 + X,Y が3着以内、順不同)
    # =========================================================
    print("\n[Test 5] 3連複 1-X-Y")
    combos = [
        ("1=2=3", "1,2,3"),
        ("1=2=4", "1,2,4"),
        ("1=2=5", "1,2,5"),
        ("1=2=6", "1,2,6"),
        ("1=3=4", "1,3,4"),
        ("1=3=5", "1,3,5"),
        ("1=4=5", "1,4,5"),
    ]
    for combo_str, members in combos:
        a, b, c = members.split(",")
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            ),
            top3 AS (
                SELECT race_id,
                       SUM(CASE WHEN boat_number IN ({a},{b},{c}) AND finishing_position<=3 THEN 1 ELSE 0 END) as hits
                FROM race_results
                GROUP BY race_id
            )
            SELECT CASE WHEN top3.hits = 3 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            LEFT JOIN top3 ON r.race_id = top3.race_id
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='trio' AND pp.combination='{combo_str}'
            WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
        """)
        p = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(p)
        show(f"3連複 {combo_str}", r)

    # =========================================================
    # 検証6: 3連単 1-X-Y 上位
    # =========================================================
    print("\n[Test 6] 3連単 1-X-Y 主要組合せ")
    tri_combos = [
        "1-2-3", "1-2-4", "1-2-5",
        "1-3-2", "1-3-4", "1-3-5",
        "1-4-2", "1-4-3", "1-4-5",
        "1-5-2", "1-5-3",
    ]
    for combo in tri_combos:
        a, b, c = combo.split("-")
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            ),
            hit AS (
                SELECT r.race_id
                FROM races r
                JOIN race_results rr1 ON r.race_id=rr1.race_id AND rr1.boat_number={a} AND rr1.finishing_position=1
                JOIN race_results rr2 ON r.race_id=rr2.race_id AND rr2.boat_number={b} AND rr2.finishing_position=2
                JOIN race_results rr3 ON r.race_id=rr3.race_id AND rr3.boat_number={c} AND rr3.finishing_position=3
            )
            SELECT CASE WHEN hit.race_id IS NOT NULL THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            LEFT JOIN hit ON r.race_id = hit.race_id
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='trifecta' AND pp.combination='{combo}'
            WHERE {YEAR_FILTER} AND {FAVORITE_BAND}
        """)
        p = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(p)
        show(f"3連単 {combo}", r)

    conn.close()


if __name__ == "__main__":
    main()
