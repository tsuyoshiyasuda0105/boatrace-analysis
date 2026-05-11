"""
モーター改装による性能断絶を経験的に検証するスクリプト。

仮説:
  - 各会場で年1回、全モーター入れ替えがある
  - 改装をまたぐと「番号は同じでも別物理モーター」になり、性能が断絶する
  - 現在の motor_long_50_first_rate 特徴量は改装をまたぐと汚染される

検証方法:
  - 月別の同一モーター番号の1着率を見る
  - 改装月の前後で1着率が劇的に変わるか確認
"""
import sqlite3
from collections import defaultdict


# 業界慣例の改装月 (筆者調べ、正確には公式発表参照)
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


def detect_motor_jump(stadium_number: int, year: int, db_path: str) -> dict:
    """
    指定会場・年の改装月前後で、モーター性能の断絶を検証する。
    改装前 N ヶ月平均 vs 改装後 N ヶ月平均 の差を見る。
    """
    rep_month = REPLACEMENT_MONTH[stadium_number]
    conn = sqlite3.connect(db_path)

    # 改装前 (改装月の前 6 ヶ月)
    before_start = f"{year - (1 if rep_month <= 6 else 0)}-{((rep_month - 6 - 1) % 12 + 1):02d}-01"
    if rep_month - 6 <= 0:
        before_start = f"{year - 1}-{rep_month - 6 + 12:02d}-01"
    before_end = f"{year}-{rep_month:02d}-01"

    # 改装後 (改装月の後 6 ヶ月)
    after_start = f"{year}-{rep_month:02d}-01"
    after_end_month = rep_month + 6
    after_end_year = year
    if after_end_month > 12:
        after_end_month -= 12
        after_end_year += 1
    after_end = f"{after_end_year}-{after_end_month:02d}-01"

    def winrate(start, end):
        cur = conn.execute(
            """
            SELECT COUNT(*) as n,
                   AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
                   AVG(e.assigned_motor_top_2_percent) as avg_motor_top2
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id
            JOIN race_results res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
            WHERE r.stadium_number = ?
              AND r.race_date >= ? AND r.race_date < ?
              AND e.boat_number = 1
            """,
            (stadium_number, start, end),
        )
        return cur.fetchone()

    n_before, wr_before, m_before = winrate(before_start, before_end)
    n_after, wr_after, m_after = winrate(after_start, after_end)
    conn.close()
    return {
        "stadium": stadium_number,
        "name": STADIUM_NAMES[stadium_number],
        "year": year,
        "rep_month": rep_month,
        "before": {"n": n_before, "boat1_winrate": wr_before, "avg_motor_top2": m_before, "period": f"{before_start}~{before_end}"},
        "after":  {"n": n_after,  "boat1_winrate": wr_after,  "avg_motor_top2": m_after,  "period": f"{after_start}~{after_end}"},
    }


def show_motor_persistence(stadium_number: int, year: int, db_path: str):
    """
    同じモーター番号の1着率が改装をまたいで変わるか確認。
    上位5モーター番号（最も多く出走したもの）で見る。
    """
    rep_month = REPLACEMENT_MONTH[stadium_number]
    conn = sqlite3.connect(db_path)

    print(f"\n--- Motor持続性検証: {STADIUM_NAMES[stadium_number]} (stadium={stadium_number}, 改装月={rep_month}) ---")
    # 改装の前半期 vs 後半期に出走多いモーター番号トップ
    before_end = f"{year}-{rep_month:02d}-01"
    after_end_year = year + (1 if rep_month + 6 > 12 else 0)
    after_end_month = (rep_month + 6 - 1) % 12 + 1
    after_end = f"{after_end_year}-{after_end_month:02d}-01"

    # 改装前後で出走数の多い上位5モーター
    cur = conn.execute(
        """
        SELECT e.assigned_motor_number as mno,
               COUNT(CASE WHEN r.race_date < ? THEN 1 END) as n_before,
               COUNT(CASE WHEN r.race_date >= ? AND r.race_date < ? THEN 1 END) as n_after,
               AVG(CASE WHEN r.race_date < ? AND res.finishing_position=1 THEN 1.0
                        WHEN r.race_date < ? THEN 0.0 END) as wr_before,
               AVG(CASE WHEN r.race_date >= ? AND r.race_date < ? AND res.finishing_position=1 THEN 1.0
                        WHEN r.race_date >= ? AND r.race_date < ? THEN 0.0 END) as wr_after
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id
        JOIN race_results res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
        WHERE r.stadium_number = ?
          AND r.race_date >= ? AND r.race_date < ?
          AND e.boat_number = 1
        GROUP BY e.assigned_motor_number
        HAVING n_before >= 5 AND n_after >= 5
        ORDER BY (n_before + n_after) DESC
        LIMIT 8
        """,
        (
            before_end, before_end, after_end, before_end, before_end,
            before_end, after_end, before_end, after_end,
            stadium_number, f"{year - 1}-{rep_month:02d}-01", after_end,
        ),
    )

    print(f"{'mno':>5} {'n_pre':>6} {'wr_pre':>8} {'n_post':>7} {'wr_post':>9} {'diff':>8}")
    for r in cur.fetchall():
        mno, n_b, n_a, wr_b, wr_a = r
        diff = (wr_a or 0) - (wr_b or 0)
        marker = " <<<" if abs(diff) > 0.20 else ""
        print(f"{mno:>5} {n_b:>6} {wr_b or 0:>8.3f} {n_a:>7} {wr_a or 0:>9.3f} {diff:>+8.3f}{marker}")
    conn.close()


if __name__ == "__main__":
    DB = "data/boatrace.db"
    print("=" * 60)
    print("モーター改装による性能断絶 経験的検証")
    print("=" * 60)

    # 検証対象: 平和島(4, 3月改装) と 多摩川(5, 4月改装) で 2024年
    for sid in [4, 5, 24, 22]:  # 平和島・多摩川・大村・福岡
        show_motor_persistence(sid, 2024, DB)
