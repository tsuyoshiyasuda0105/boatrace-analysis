"""1-4 / 4-1 組合せ分析。

2連単 1-4 (1号艇 1着・4号艇 2着) と 4-1 (4号艇 1着・1号艇 2着)、
および 3連単 1-4-x (3点流し) / 4-1-x (3点流し) の ROI を、
会場・class・モーターでブレークダウンして優位ゾーンを探す。

なぜ興味深いか:
  - 1号艇は本命 (1着率 ~55%) → 1着固定の予想は当たりやすい
  - 4号艇 (カド) は 2着抜けが多発する → 1-4 は隠れた美味しさ?
  - 4号艇まくり時は 1号艇が 2 着残り → 4-1 もパターン豊富
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config


JST = ZoneInfo("Asia/Tokyo")


def _now_jst() -> datetime:
    return datetime.now(JST)

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


def query_combo_roi(conn, bet_type: str, combination: str, where_extra: str = "",
                    extra_args: list = None) -> dict:
    """combination の bet を全該当レースで打ったときの ROI を計算。"""
    extra_args = extra_args or []
    sql = f"""
        SELECT COUNT(DISTINCT r.race_id) AS n_races,
               COUNT(pp.payout) AS hits,
               COALESCE(SUM(pp.payout), 0) AS pay
          FROM races r
          LEFT JOIN race_payouts pp ON pp.race_id=r.race_id
                                  AND pp.bet_type=? AND pp.combination=?
         WHERE 1=1 {where_extra}
    """
    args = [bet_type, combination] + extra_args
    n, hits, pay = conn.execute(sql, args).fetchone()
    n = n or 0; hits = hits or 0; pay = int(pay or 0)
    roi = pay / (100 * n) * 100 if n else 0.0
    avg = pay / hits if hits else 0
    return {"n": n, "hits": hits, "pay": pay, "roi": roi, "avg": avg}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports")
    args = parser.parse_args()

    conn = _conn()
    print("=== 1-4 / 4-1 組合せ分析 ===\n")

    # 1. 全体ベースライン (全レース×全条件)
    print("【全体ベースライン (全レース)】")
    print(f"  {'賭式':<15} {'組合せ':<10} {'n':>7} {'的中率':>7} {'平均配当':>8} {'ROI':>7}")
    for bt, combo in [("exacta", "1-4"), ("exacta", "4-1"),
                       ("quinella", "1=4"),
                       ("trifecta", "1-4-2"), ("trifecta", "1-4-3"),
                       ("trifecta", "1-4-5"),
                       ("trifecta", "4-1-2"), ("trifecta", "4-1-3"),
                       ("trifecta", "4-1-5")]:
        r = query_combo_roi(conn, bt, combo)
        hit_rate = r["hits"]/r["n"]*100 if r["n"] else 0
        print(f"  {bt:<15} {combo:<10} {r['n']:7,} {hit_rate:6.1f}% "
              f"{r['avg']:7.0f}円 {r['roi']:6.1f}%")

    # 2. 会場別 2連単 1-4 / 4-1 / 1=4
    print("\n【会場別 2連単 1-4 / 4-1 / 1=4 (2連複) ROI】")
    print(f"  {'会場':>6} {'1-4 ROI':>8} {'4-1 ROI':>8} {'1=4 ROI':>8} "
          f"{'(n)':>7}")
    rows = []
    for sta in range(1, 25):
        r14 = query_combo_roi(conn, "exacta", "1-4",
                              "AND r.stadium_number=?", [sta])
        r41 = query_combo_roi(conn, "exacta", "4-1",
                              "AND r.stadium_number=?", [sta])
        rq = query_combo_roi(conn, "quinella", "1=4",
                              "AND r.stadium_number=?", [sta])
        rows.append({"sta": sta, "r14": r14, "r41": r41, "rq": rq})
    rows.sort(key=lambda x: -max(x["r14"]["roi"], x["r41"]["roi"], x["rq"]["roi"]))
    for x in rows[:24]:
        sta = x["sta"]
        flag = ""
        if x["r14"]["roi"] > 100 or x["r41"]["roi"] > 100 or x["rq"]["roi"] > 100:
            flag = " ★"
        print(f"  {STAD.get(sta,sta):>6} {x['r14']['roi']:7.1f}% "
              f"{x['r41']['roi']:7.1f}% {x['rq']['roi']:7.1f}% "
              f"({x['r14']['n']:,}){flag}")

    # 3. 1号艇 A1 × 4号艇 class 別 (1-4 / 4-1)
    print("\n【1号艇 A1 × 4号艇 class 別 2連単 1-4 / 4-1】")
    print(f"  {'4号艇':>5} {'n':>6} {'1-4 ROI':>8} {'4-1 ROI':>8} {'1=4 ROI':>8}")
    for c4 in [1, 2, 3, 4]:
        where = ("""
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=1 AND class_number=1
            )
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=4 AND class_number=?
            )
        """)
        r14 = query_combo_roi(conn, "exacta", "1-4", where, [c4])
        r41 = query_combo_roi(conn, "exacta", "4-1", where, [c4])
        rq = query_combo_roi(conn, "quinella", "1=4", where, [c4])
        cls_map = {1:"A1",2:"A2",3:"B1",4:"B2"}
        if r14["n"] < 100:
            continue
        print(f"  {cls_map[c4]:>5} {r14['n']:6,} "
              f"{r14['roi']:7.1f}% {r41['roi']:7.1f}% {rq['roi']:7.1f}%")

    # 4. 1号艇 A1 × 4号艇 A1 × 会場別 (最強コンビ)
    print("\n【1号艇 A1 × 4号艇 A1 × 会場別 2連単 1-4 / 4-1】(n≥80)")
    print(f"  {'会場':>6} {'n':>5} {'1-4 ROI':>8} {'4-1 ROI':>8} {'1-4配当':>8} {'4-1配当':>8}")
    out = []
    for sta in range(1, 25):
        where = ("""
            AND r.stadium_number=?
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=1 AND class_number=1
            )
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=4 AND class_number=1
            )
        """)
        r14 = query_combo_roi(conn, "exacta", "1-4", where, [sta])
        r41 = query_combo_roi(conn, "exacta", "4-1", where, [sta])
        if r14["n"] < 80:
            continue
        out.append({"sta": sta, "n": r14["n"], "r14": r14["roi"],
                    "r41": r41["roi"], "a14": r14["avg"], "a41": r41["avg"]})
    out.sort(key=lambda x: -max(x["r14"], x["r41"]))
    for x in out:
        flag = " ★" if max(x["r14"], x["r41"]) > 110 else ""
        print(f"  {STAD.get(x['sta'],x['sta']):>6} {x['n']:5,} "
              f"{x['r14']:7.1f}% {x['r41']:7.1f}% "
              f"{x['a14']:7.0f}円 {x['a41']:7.0f}円{flag}")

    # 5. 4号艇のモーター帯で 4-1 ROI が変わるか
    print("\n【4号艇 motor 2連率帯 × 2連単 4-1 ROI】(全 1号艇 A1)")
    print(f"  {'motor帯':>10} {'n':>6} {'4-1 ROI':>8} {'1-4 ROI':>8} {'4-1配当':>8}")
    for lo, hi, lbl in [(45, 100, "A:45+"), (40, 45, "B:40-45"),
                         (35, 40, "C:35-40"), (0, 35, "D:<35")]:
        where = ("""
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=1 AND class_number=1
            )
            AND r.race_id IN (
              SELECT race_id FROM race_entries
               WHERE boat_number=4
                 AND assigned_motor_top_2_percent >= ?
                 AND assigned_motor_top_2_percent < ?
            )
        """)
        r14 = query_combo_roi(conn, "exacta", "1-4", where, [lo, hi])
        r41 = query_combo_roi(conn, "exacta", "4-1", where, [lo, hi])
        if r14["n"] < 50:
            continue
        print(f"  {lbl:>10} {r14['n']:6,} "
              f"{r41['roi']:7.1f}% {r14['roi']:7.1f}% {r41['avg']:7.0f}円")

    conn.close()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f"analyze_14_41_{_now_jst():%Y%m%d_%H%M}.md"
    fpath.write_text("# 1-4 / 4-1 組合せ分析 (詳細はコンソール参照)\n",
                     encoding="utf-8")
    print(f"\nレポート骨子: {fpath}")


if __name__ == "__main__":
    main()
