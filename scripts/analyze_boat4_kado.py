"""4号艇カドまくり徹底分析。

選手強さ (class / national_top_1 / local_top_1) × モーター強さ
(assigned_motor_top_2) で 4号艇 1着率と単勝 ROI、3連単頭 ROI を計算。
「4号艇まくり狙い」の真の優位ゾーンを発見する。

会場別ブレークダウンも併せて出力 (どの会場で 4号艇が美味しいか)。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config

STAD = {1:"桐生",2:"戸田",3:"江戸川",4:"平和島",5:"多摩川",6:"浜名湖",7:"蒲郡",8:"常滑",
        9:"津",10:"三国",11:"びわこ",12:"住之江",13:"尼崎",14:"鳴門",15:"丸亀",16:"児島",
        17:"宮島",18:"徳山",19:"下関",20:"若松",21:"芦屋",22:"福岡",23:"唐津",24:"大村"}


def _conn():
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from src.db.connection import connect as db_connect
            return db_connect()
        except Exception:  # noqa: BLE001
            pass
    return sqlite3.connect(config.DB_PATH)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports")
    parser.add_argument("--n-min", type=int, default=200)
    args = parser.parse_args()

    conn = _conn()
    print("=== 4号艇カド分析 ===\n")

    # 1. 全体ベースライン (4号艇1着率 / 単勝ROI)
    base = conn.execute("""
        SELECT COUNT(*) total, SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) wins,
               COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                 THEN COALESCE(pw.payout,0) ELSE 0 END), 0) winpay
          FROM race_entries e4
          JOIN race_results rr ON e4.race_id=rr.race_id AND e4.boat_number=rr.boat_number
          LEFT JOIN race_payouts pw ON pw.race_id=e4.race_id AND pw.bet_type='win' AND pw.combination='4'
         WHERE e4.boat_number=4
    """).fetchone()
    n_total, n_wins, win_pay = base
    print(f"【全体ベースライン】 4号艇 n={n_total:,}  1着={n_wins:,}  "
          f"勝率={100*n_wins/n_total:.2f}%  "
          f"単勝ROI={win_pay/(100*n_total)*100:.1f}%")

    # 2. 4号艇クラス×モーター帯
    print("\n【4号艇 class × motor 2連率帯】")
    print(f"  {'class':>5} {'motor帯':>10} {'n':>7} {'1着率':>7} {'単勝ROI':>8} "
          f"{'平均配当':>8}")
    rows = conn.execute("""
        SELECT e4.class_number AS cls,
               CASE WHEN e4.assigned_motor_top_2_percent >= 45 THEN 'A:45+'
                    WHEN e4.assigned_motor_top_2_percent >= 40 THEN 'B:40-45'
                    WHEN e4.assigned_motor_top_2_percent >= 35 THEN 'C:35-40'
                    ELSE 'D:<35' END AS motor_band,
               COUNT(*) AS n,
               SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                 THEN COALESCE(pw.payout,0) ELSE 0 END), 0) AS winpay
          FROM race_entries e4
          JOIN race_results rr ON e4.race_id=rr.race_id AND e4.boat_number=rr.boat_number
          LEFT JOIN race_payouts pw ON pw.race_id=e4.race_id AND pw.bet_type='win' AND pw.combination='4'
         WHERE e4.boat_number=4 AND e4.assigned_motor_top_2_percent IS NOT NULL
         GROUP BY cls, motor_band
         ORDER BY cls, motor_band
    """).fetchall()
    cls_map = {1:"A1",2:"A2",3:"B1",4:"B2"}
    findings = []
    for cls, mb, n, wins, pay in rows:
        if n < args.n_min:
            continue
        rate = 100*wins/n if n else 0
        roi = pay/(100*n)*100 if n else 0
        avg = pay/wins if wins else 0
        cls_lbl = cls_map.get(cls, "?")
        print(f"  {cls_lbl:>5} {mb:>10} {n:7,} {rate:6.2f}% {roi:7.1f}% {avg:7.0f}円")
        findings.append({"cls": cls_lbl, "motor": mb, "n": n, "wins": wins,
                         "rate": rate, "roi": roi, "avg": avg})

    # 3. 強さ重ねがけ: A1 + motor 40+ + 国1着率高い 4号艇
    print("\n【4号艇 強さ重ね合わせ: A1+ × motor2≥40 × natl1着率帯】")
    print(f"  {'国1着率':>7} {'n':>6} {'1着率':>7} {'単勝ROI':>8}")
    rows = conn.execute("""
        SELECT CASE WHEN e4.national_top_1_percent >= 7 THEN 'A:7+'
                    WHEN e4.national_top_1_percent >= 6 THEN 'B:6-7'
                    WHEN e4.national_top_1_percent >= 5 THEN 'C:5-6'
                    ELSE 'D:<5' END AS band,
               COUNT(*) AS n,
               SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                 THEN COALESCE(pw.payout,0) ELSE 0 END), 0) AS winpay
          FROM race_entries e4
          JOIN race_results rr ON e4.race_id=rr.race_id AND e4.boat_number=rr.boat_number
          LEFT JOIN race_payouts pw ON pw.race_id=e4.race_id AND pw.bet_type='win' AND pw.combination='4'
         WHERE e4.boat_number=4 AND e4.class_number=1
           AND e4.assigned_motor_top_2_percent >= 40
         GROUP BY band ORDER BY band
    """).fetchall()
    for band, n, wins, pay in rows:
        if n < 50:
            continue
        rate = 100*wins/n if n else 0
        roi = pay/(100*n)*100 if n else 0
        print(f"  {band:>7} {n:6,} {rate:6.2f}% {roi:7.1f}%")

    # 4. 会場別: A1 × motor 40+ 4号艇 1着率
    print("\n【会場別: 4号艇 A1 × motor2≥40 1着率と単勝ROI】 (n_min 100)")
    print(f"  {'会場':>6} {'n':>5} {'1着率':>7} {'単勝ROI':>8} {'平均配当':>8}")
    rows = conn.execute("""
        SELECT r.stadium_number AS sta, COUNT(*) AS n,
               SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                 THEN COALESCE(pw.payout,0) ELSE 0 END), 0) AS winpay
          FROM races r
          JOIN race_entries e4 ON e4.race_id=r.race_id AND e4.boat_number=4
          JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number=4
          LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='4'
         WHERE e4.class_number=1 AND e4.assigned_motor_top_2_percent >= 40
         GROUP BY r.stadium_number
         ORDER BY COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                     THEN COALESCE(pw.payout,0) ELSE 0 END), 0)*1.0
                  /(100.0*COUNT(*)) DESC
    """).fetchall()
    venue_rows = []
    for sta, n, wins, pay in rows:
        if n < 30:  # 各会場 A1×motor40+ は標本小なので閾値下げる
            continue
        rate = 100*wins/n if n else 0
        roi = pay/(100*n)*100 if n else 0
        avg = pay/wins if wins else 0
        print(f"  {STAD.get(sta,sta):>6} {n:5,} {rate:6.2f}% {roi:7.1f}% {avg:7.0f}円")
        venue_rows.append({"venue": STAD.get(sta, sta), "n": n, "rate": rate,
                            "roi": roi, "avg": avg})

    # 5. 3連単頭 (4-x-y all 20点) ROI: 4号艇 A1 × motor 40+
    print("\n【3連単 4頭-全流し (20点) ROI: 4号艇 A1 × motor2≥40 会場別】")
    rows = conn.execute("""
        SELECT r.stadium_number AS sta, COUNT(*) AS n_races,
               COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                 THEN COALESCE(pt.payout,0) ELSE 0 END), 0) AS pay
          FROM races r
          JOIN race_entries e4 ON e4.race_id=r.race_id AND e4.boat_number=4
          JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number=4
          LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta'
                                  AND pt.combination LIKE ?
         WHERE e4.class_number=1 AND e4.assigned_motor_top_2_percent >= 40
         GROUP BY r.stadium_number
         ORDER BY COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                     THEN COALESCE(pt.payout,0) ELSE 0 END), 0)*1.0
                  /(2000.0*COUNT(*)) DESC
    """, ("4-%",)).fetchall()
    print(f"  {'会場':>6} {'n':>5} {'頭頻度':>7} {'4頭流し ROI':>11}")
    for sta, n, pay in rows:
        if n < 30:
            continue
        cost = 2000 * n
        roi = pay/cost*100 if cost else 0
        # n_races where 4 was head — already includes; pay sums payouts when 4 was head
        # We approximate "head frequency" via wins query separately
        wins = conn.execute("""
            SELECT COUNT(*) FROM race_entries e4
              JOIN race_results rr ON e4.race_id=rr.race_id AND rr.boat_number=4
              JOIN races r ON r.race_id=e4.race_id
             WHERE e4.boat_number=4 AND e4.class_number=1
               AND e4.assigned_motor_top_2_percent >= 40
               AND r.stadium_number=? AND rr.finishing_position=1
        """, (sta,)).fetchone()[0]
        head_freq = 100*wins/n if n else 0
        flag = " ★" if roi > 100 else ""
        print(f"  {STAD.get(sta,sta):>6} {n:5,} {head_freq:6.1f}% {roi:10.1f}%{flag}")

    conn.close()

    # markdown 出力
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f"boat4_kado_{datetime.now():%Y%m%d_%H%M}.md"
    fpath.write_text("# 4号艇カド分析レポート (詳細はコンソール出力参照)\n",
                     encoding="utf-8")
    print(f"\nレポート骨子: {fpath}")


if __name__ == "__main__":
    main()
