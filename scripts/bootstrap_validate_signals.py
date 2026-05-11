"""
Phase 1 シグナルの Bootstrap CI 検証

各シグナル/戦略について:
  - 点推定 ROI
  - Bootstrap 95% CI (n=2000)
  - P(ROI > 0): 全国控除率突破確率
  - P(ROI > -5%): 損失最小化達成確率

これにより「統計的に意味のある改善か」を確定する。
"""
import sqlite3
import random
import statistics
from typing import List, Tuple

DB = "data/boatrace.db"
N_BOOTSTRAP = 2000
random.seed(42)


def bootstrap_ci(payouts: List[float], bet_amount: float = 100) -> dict:
    """Bootstrap で ROI の信頼区間を計算"""
    n = len(payouts)
    if n == 0:
        return {"n": 0, "roi_point": None, "ci_lower": None, "ci_upper": None,
                "p_positive": None, "p_above_neg5": None}
    rois = []
    for _ in range(N_BOOTSTRAP):
        sample = random.choices(payouts, k=n)
        avg_payout = sum(sample) / n
        roi = avg_payout / bet_amount - 1.0
        rois.append(roi)
    rois.sort()
    point = sum(payouts) / n / bet_amount - 1.0
    ci_lower = rois[int(N_BOOTSTRAP * 0.025)]
    ci_upper = rois[int(N_BOOTSTRAP * 0.975)]
    p_positive = sum(1 for r in rois if r > 0) / N_BOOTSTRAP
    p_above_neg5 = sum(1 for r in rois if r > -0.05) / N_BOOTSTRAP
    return {
        "n": n,
        "roi_point": point,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "p_positive": p_positive,
        "p_above_neg5": p_above_neg5,
    }


def fetch_payouts(conn, where_clause: str) -> List[float]:
    """指定フィルタ条件で 1号艇単勝の払戻(円)リストを取得"""
    sql = f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END as p
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        WHERE {where_clause}
    """
    return [float(row[0]) for row in conn.execute(sql).fetchall()]


def fetch_payouts_boat(conn, boat_number: int, where_clause: str) -> List[float]:
    """指定艇の単勝払戻リスト"""
    sql = f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END as p
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {boat_number}
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {boat_number}
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = {boat_number}
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='{boat_number}'
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        WHERE {where_clause}
    """
    return [float(row[0]) for row in conn.execute(sql).fetchall()]


def print_result(label: str, result: dict):
    if result["n"] == 0:
        print(f"  {label:<55} [no data]")
        return
    n = result["n"]
    pt = result["roi_point"]
    lo = result["ci_lower"]
    hi = result["ci_upper"]
    pp = result["p_positive"]
    pn5 = result["p_above_neg5"]
    indicator = ""
    if hi > 0: indicator = " *** CI upper > 0!"
    elif pn5 > 0.5: indicator = " *  break-even圏"
    print(f"  {label:<55} n={n:>7,}  ROI={pt:>+7.2%}  CI=[{lo:>+7.2%}, {hi:>+7.2%}]  P>0={pp:>4.1%}{indicator}")


def main():
    conn = sqlite3.connect(DB)

    # ==================================================
    # ベースライン
    # ==================================================
    print("=" * 110)
    print("ベースライン (1号艇単勝)")
    print("=" * 110)
    for label, where in [
        ("全レース", "1=1"),
        ("Sweet Spot (4会場除外)", "r.stadium_number NOT IN (2,7,10,21)"),
    ]:
        payouts = fetch_payouts(conn, where)
        result = bootstrap_ci(payouts)
        print_result(label, result)

    # ==================================================
    # Phase 1-1: モーター 35-45% フィルタ
    # ==================================================
    print()
    print("=" * 110)
    print("[Phase 1-1] モーター連対率フィルタ")
    print("=" * 110)
    for label, where in [
        ("Sweet Spot + Motor>=30%", "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=30"),
        ("Sweet Spot + Motor>=35%", "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=35"),
        ("Sweet Spot + Motor 35-45%", "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=35 AND e.assigned_motor_top_2_percent<45"),
        ("Sweet Spot + Motor 35-50%", "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=35 AND e.assigned_motor_top_2_percent<50"),
        ("Sweet Spot + Motor 40-50%", "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=40 AND e.assigned_motor_top_2_percent<50"),
        ("Sweet Spot + Motor>=45%",   "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent>=45"),
    ]:
        payouts = fetch_payouts(conn, where)
        result = bootstrap_ci(payouts)
        print_result(label, result)

    # ==================================================
    # Phase 1-2: 差し水面除外
    # ==================================================
    print()
    print("=" * 110)
    print("[Phase 1-2] 差し水面 (in_strength=low) 除外")
    print("=" * 110)
    for label, where in [
        ("Sweet Spot 単独", "r.stadium_number NOT IN (2,7,10,21)"),
        ("Sweet Spot + 差し水面除外", "r.stadium_number NOT IN (2,7,10,21) AND s.in_strength != 'low'"),
        ("Sweet Spot + low + mid除外 (high以上)", "r.stadium_number NOT IN (2,7,10,21) AND s.in_strength IN ('high','very_high')"),
        ("very_high のみ (大村等)", "s.in_strength = 'very_high'"),
    ]:
        payouts = fetch_payouts(conn, where)
        result = bootstrap_ci(payouts)
        print_result(label, result)

    # ==================================================
    # Phase 1-3: チルト戦略
    # ==================================================
    print()
    print("=" * 110)
    print("[Phase 1-3] チルト戦略 (4-6号艇 単勝)")
    print("=" * 110)
    for boat in [4, 5, 6]:
        print(f"\n--- 艇 {boat} ---")
        for label, where in [
            (f"艇{boat} 全レース", "1=1"),
            (f"艇{boat} tilt 標準 (<=-0.5)", "p.tilt_adjustment <= -0.5"),
            (f"艇{boat} tilt 0.5-1.5", "p.tilt_adjustment >= 0.5 AND p.tilt_adjustment <= 1.5"),
            (f"艇{boat} tilt >= 1.0", "p.tilt_adjustment >= 1.0"),
            (f"艇{boat} tilt >= 1.5", "p.tilt_adjustment >= 1.5"),
            (f"艇{boat} tilt >= 2.0", "p.tilt_adjustment >= 2.0"),
            (f"艇{boat} tilt = 3.0", "p.tilt_adjustment = 3.0"),
        ]:
            payouts = fetch_payouts_boat(conn, boat, where)
            result = bootstrap_ci(payouts)
            print_result(label, result)

    # ==================================================
    # Phase 1-4: 展示タイム
    # ==================================================
    print()
    print("=" * 110)
    print("[Phase 1-4] 展示タイム差フィルタ (1号艇)")
    print("=" * 110)
    # 展示タイム最速差を計算するため事前に SQL CTE 必要
    for label, where in [
        ("Sweet Spot + 展示±0.05秒以内",
         """r.stadium_number NOT IN (2,7,10,21)
            AND (p.exhibition_time - (SELECT MIN(p2.exhibition_time) FROM race_previews p2
                                       WHERE p2.race_id = r.race_id AND p2.exhibition_time IS NOT NULL)) <= 0.05"""),
        ("Sweet Spot + Motor>=35% + 展示±0.05秒",
         """r.stadium_number NOT IN (2,7,10,21)
            AND e.assigned_motor_top_2_percent >= 35
            AND (p.exhibition_time - (SELECT MIN(p2.exhibition_time) FROM race_previews p2
                                       WHERE p2.race_id = r.race_id AND p2.exhibition_time IS NOT NULL)) <= 0.05"""),
        ("[全部入り] Sweet Spot + Motor35-50 + 展示±0.05 + 差し水面除外",
         """r.stadium_number NOT IN (2,7,10,21)
            AND s.in_strength != 'low'
            AND e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
            AND (p.exhibition_time - (SELECT MIN(p2.exhibition_time) FROM race_previews p2
                                       WHERE p2.race_id = r.race_id AND p2.exhibition_time IS NOT NULL)) <= 0.05"""),
    ]:
        payouts = fetch_payouts(conn, where)
        result = bootstrap_ci(payouts)
        print_result(label, result)

    # ==================================================
    # 究極の組合せ
    # ==================================================
    print()
    print("=" * 110)
    print("[Final] 究極の組合せ戦略")
    print("=" * 110)
    final_where = """
        r.stadium_number NOT IN (2,7,10,21)
        AND s.in_strength != 'low'
        AND e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
        AND (p.exhibition_time - (SELECT MIN(p2.exhibition_time) FROM race_previews p2
                                   WHERE p2.race_id = r.race_id AND p2.exhibition_time IS NOT NULL)) <= 0.05
        AND p.wind_speed BETWEEN 1 AND 3
    """
    payouts = fetch_payouts(conn, final_where)
    result = bootstrap_ci(payouts)
    print_result("全条件: SS + 水面 + Motor35-50 + 展示±0.05 + 微風1-3m", result)

    conn.close()


if __name__ == "__main__":
    main()
