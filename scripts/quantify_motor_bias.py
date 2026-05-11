"""
モーター改装境界における性能断絶の統計的定量化。

目的:
  - 全会場・全年で「改装前後の同モーター番号の1着率差」の絶対値を集計
  - 「改装をまたがない時の月間変動」と比較してベースラインからの逸脱を測定
  - 現在のモデルが受けている汚染の規模を推定
"""
import sqlite3
import statistics
from collections import defaultdict


REPLACEMENT_MONTH = {
    1: 3, 2: 5, 3: 11, 4: 3, 5: 4, 6: 4, 7: 6, 8: 7, 9: 8, 10: 3,
    11: 10, 12: 5, 13: 7, 14: 6, 15: 9, 16: 4, 17: 3, 18: 10, 19: 6,
    20: 4, 21: 11, 22: 2, 23: 12, 24: 7,
}

STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def pair_motor_winrates(stadium: int, year: int, db_path: str):
    """改装前後の同モーター番号ペアの1着率差を返す。"""
    rep_month = REPLACEMENT_MONTH[stadium]
    rep_date = f"{year}-{rep_month:02d}-01"
    before_start = f"{year - 1}-{rep_month:02d}-01"
    after_end_y = year + (1 if rep_month + 6 > 12 else 0)
    after_end_m = (rep_month + 6 - 1) % 12 + 1
    after_end = f"{after_end_y}-{after_end_m:02d}-01"

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        SELECT e.assigned_motor_number as mno,
               SUM(CASE WHEN r.race_date < ? THEN 1 ELSE 0 END) as n_before,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? THEN 1 ELSE 0 END) as n_after,
               SUM(CASE WHEN r.race_date < ? AND res.finishing_position=1 THEN 1 ELSE 0 END) as w_before,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? AND res.finishing_position=1 THEN 1 ELSE 0 END) as w_after
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id
        JOIN race_results res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
        WHERE r.stadium_number = ?
          AND r.race_date >= ? AND r.race_date < ?
          AND e.boat_number = 1
        GROUP BY e.assigned_motor_number
        HAVING n_before >= 10 AND n_after >= 10
        """,
        (rep_date, rep_date, after_end, rep_date, rep_date, after_end,
         stadium, before_start, after_end),
    )
    diffs = []
    for mno, n_b, n_a, w_b, w_a in cur.fetchall():
        if n_b and n_a:
            diff = w_a / n_a - w_b / n_b
            diffs.append(diff)
    conn.close()
    return diffs


def pair_non_replacement_winrates(stadium: int, year: int, db_path: str):
    """
    対照群: 同年内で改装をまたがない 6ヶ月窓 vs 6ヶ月窓 の差。
    例: 改装が3月なら、改装後4月-9月 と 10月-翌3月 を比較。
    これは「改装無しの場合の自然変動」を表す。
    """
    rep_month = REPLACEMENT_MONTH[stadium]
    period1_start = f"{year}-{rep_month:02d}-01"
    period1_end_m = rep_month + 6
    period1_end_y = year + (1 if period1_end_m > 12 else 0)
    period1_end_m = (period1_end_m - 1) % 12 + 1
    period1_end = f"{period1_end_y}-{period1_end_m:02d}-01"
    period2_start = period1_end
    period2_end_m = period1_end_m + 6
    period2_end_y = period1_end_y + (1 if period2_end_m > 12 else 0)
    period2_end_m = (period2_end_m - 1) % 12 + 1
    period2_end = f"{period2_end_y}-{period2_end_m:02d}-01"

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        SELECT e.assigned_motor_number as mno,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? THEN 1 ELSE 0 END) as n1,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? THEN 1 ELSE 0 END) as n2,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? AND res.finishing_position=1 THEN 1 ELSE 0 END) as w1,
               SUM(CASE WHEN r.race_date >= ? AND r.race_date < ? AND res.finishing_position=1 THEN 1 ELSE 0 END) as w2
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id
        JOIN race_results res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
        WHERE r.stadium_number = ?
          AND r.race_date >= ? AND r.race_date < ?
          AND e.boat_number = 1
        GROUP BY e.assigned_motor_number
        HAVING n1 >= 10 AND n2 >= 10
        """,
        (period1_start, period1_end, period2_start, period2_end,
         period1_start, period1_end, period2_start, period2_end,
         stadium, period1_start, period2_end),
    )
    diffs = []
    for mno, n1, n2, w1, w2 in cur.fetchall():
        if n1 and n2:
            diff = w2 / n2 - w1 / n1
            diffs.append(diff)
    conn.close()
    return diffs


def main():
    DB = "data/boatrace.db"
    print("=" * 70)
    print("モーター改装影響の統計的定量化")
    print("=" * 70)

    all_replacement_diffs = []
    all_non_replacement_diffs = []

    print(f"\n{'Stadium':<8} {'改装月':>6} {'年':>5} | {'改装またぎペア':<14} {'対照群(同期間内)':<18}")
    print(f"{'':<8} {'':>6} {'':>5} | {'n  平均|diff|':<14} {'n  平均|diff|':<18}")
    print("-" * 70)

    for sid in range(1, 25):
        for year in [2023, 2024, 2025]:
            rep_diffs = pair_motor_winrates(sid, year, DB)
            non_diffs = pair_non_replacement_winrates(sid, year, DB)
            if not rep_diffs and not non_diffs:
                continue
            all_replacement_diffs.extend(rep_diffs)
            all_non_replacement_diffs.extend(non_diffs)
            abs_rep = [abs(d) for d in rep_diffs]
            abs_non = [abs(d) for d in non_diffs]
            r_mean = statistics.mean(abs_rep) if abs_rep else 0
            n_mean = statistics.mean(abs_non) if abs_non else 0
            print(f"{STADIUM_NAMES[sid]:<8} {REPLACEMENT_MONTH[sid]:>6} {year:>5} | "
                  f"{len(rep_diffs):>3} {r_mean:>10.3f} | "
                  f"{len(non_diffs):>3} {n_mean:>10.3f}")

    # 全体集計
    print("\n" + "=" * 70)
    print("全体集計")
    print("=" * 70)
    if all_replacement_diffs:
        abs_rep_all = [abs(d) for d in all_replacement_diffs]
        print(f"改装またぎペア: n={len(all_replacement_diffs)}")
        print(f"  平均 |diff| = {statistics.mean(abs_rep_all):.4f}")
        print(f"  中央値 |diff| = {statistics.median(abs_rep_all):.4f}")
        print(f"  標準偏差 = {statistics.stdev(all_replacement_diffs):.4f}")
        print(f"  |diff| >= 0.20 の割合 = {sum(1 for x in abs_rep_all if x >= 0.20) / len(abs_rep_all):.2%}")
    if all_non_replacement_diffs:
        abs_non_all = [abs(d) for d in all_non_replacement_diffs]
        print(f"\n対照群 (改装無し同期間): n={len(all_non_replacement_diffs)}")
        print(f"  平均 |diff| = {statistics.mean(abs_non_all):.4f}")
        print(f"  中央値 |diff| = {statistics.median(abs_non_all):.4f}")
        print(f"  標準偏差 = {statistics.stdev(all_non_replacement_diffs):.4f}")
        print(f"  |diff| >= 0.20 の割合 = {sum(1 for x in abs_non_all if x >= 0.20) / len(abs_non_all):.2%}")

    if all_replacement_diffs and all_non_replacement_diffs:
        ratio = statistics.mean([abs(d) for d in all_replacement_diffs]) / statistics.mean([abs(d) for d in all_non_replacement_diffs])
        print(f"\n改装またぎ / 対照群 比 = {ratio:.2f}x")
        print(f"→ 改装をまたぐと、自然変動の {ratio:.2f} 倍の性能差が生じる")


if __name__ == "__main__":
    main()
