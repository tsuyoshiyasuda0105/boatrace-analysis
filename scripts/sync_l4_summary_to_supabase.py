"""src (生データ) → Supabase l4_daily_summary に L4 [A1] ROI 集計のみを同期。

Supabase Free プランの 500MB 制限内に収まるよう、生データ
(races / race_entries / race_payouts / race_results) ではなく
日別の L4 [A1] ROI 集計値のみを Supabase に置く。

L4 戦略条件 (=2026-05 時点):
  - 1号艇 A1 (class_number = 1)
  - SG/G1/G2/G3 (grade_number IN 1,2,3,4、一般戦5除外)
  - B除外会場 (2,4,7,8,10,19,21,24) 除外
  - 判定優先: T-X 1-2-3 オッズ × 100 が 500-1000円帯
            (T-X 無し時のフォールバック: race_payouts MIN が 500-1000円帯)

データソース選択 (--src オプション):
  - --src supabase (default): Supabase の生データから計算 (odds_trifecta 含む)
  - --src local: ローカル SQLite から計算 (2022-2024 等、Supabase に無い期間)

使い方:
    python scripts/sync_l4_summary_to_supabase.py --start 2025-01-01 --end 2026-12-31
    python scripts/sync_l4_summary_to_supabase.py --src local --start 2022-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from src.db.connection import connect as db_connect

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)


def compute_summary(src, start: str, end: str) -> list[dict]:
    """src (SQLite または psycopg) から日別 L4 [A1] 集計を計算"""
    placeholders = ",".join("?" for _ in EXCLUDE_B)
    sql = f"""
        SELECT r.race_date,
               COUNT(DISTINCT r.race_id) AS n_total_in_join
        FROM races r
        WHERE r.race_date BETWEEN ? AND ?
        GROUP BY r.race_date
    """
    cur = src.execute(sql, (start, end))
    n_total_by_date = {row[0]: row[1] for row in cur.fetchall()}

    # L4 該当 + 各 bet_type の hit
    # サブカテゴリ集計: 1c80 / L4 PRO / SG/G1/G2 を 1 クエリで取得
    # 一般戦 (grade=5) 観察集計 (gen_tri_* / gen_plus_tri_*) も同居取得するため
    # grade 条件は SQL では絞らず、ループ内で分岐する。
    sql = f"""
        SELECT r.race_date,
               r.race_id,
               r.race_grade_number,
               r.race_number,
               r.stadium_number,
               e.racer_number, e.avg_start_timing, e.age,
               e.national_top_1_percent AS natl_1,
               pv.start_timing_exhibition,
               pp.min_pay AS fav_pay,
               oo.min_odds AS fav_odds,
               oo.any_in_l4 AS any_in_l4,
               res1.boat_number AS w1,
               res2.boat_number AS w2,
               res3.boat_number AS w3,
               pw.payout AS win_pay,
               pe.payout AS exa_pay,
               pt.payout AS tri_pay,
               e2.national_top_2_percent AS boat2_top2
        FROM races r
        JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
        LEFT JOIN race_entries e2 ON e2.race_id=r.race_id AND e2.boat_number=2
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        LEFT JOIN (
            SELECT ef.race_id, COUNT(*) AS n_female
              FROM race_entries ef
              JOIN racers rc ON rc.racer_number = ef.racer_number
             WHERE rc.gender = 2
             GROUP BY ef.race_id
        ) fem ON fem.race_id = r.race_id
        LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
        LEFT JOIN (SELECT race_id,
                          MIN(odds) AS min_odds,
                          -- backlog item: ユーザ指摘 (2026-05-18) で「いずれかの
                          -- T-X snapshot が 5-10 帯にあれば L4 候補」に変更
                          MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4
                     FROM odds_trifecta
                   WHERE combination='1-2-3' AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                   GROUP BY race_id) oo ON oo.race_id=r.race_id
        LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
        LEFT JOIN race_payouts pe ON pe.race_id=r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
        LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
        WHERE r.race_date BETWEEN ? AND ?
          AND e.class_number = 1
          AND r.stadium_number NOT IN ({placeholders})
          AND r.race_grade_number IN (1, 2, 3, 4, 5)
          AND (pv.weather_number IS NULL OR pv.weather_number != 3)
          AND COALESCE(fem.n_female, 0) = 0   -- ♀ 案A: 女性混入レース除外
          AND (
              -- ユーザ指摘 (2026-05-18): 「いずれかの T-X snapshot で 5-10 帯」
              -- を L4 候補とする OR ロジック
              oo.any_in_l4 = 1
              OR
              -- T-X オッズ未取得 (古い日) はフォールバックで race_payouts MIN
              (oo.any_in_l4 IS NULL AND pp.min_pay BETWEEN 500 AND 999)
          )
    """
    cur = src.execute(sql, (start, end, *EXCLUDE_B))

    # 選手の過去 180 日 1コース 1着率を一括計算 (1c80 判定用)
    # racer × race_date → past 180 days winrate
    import datetime as _dt
    course1_hist = {}  # racer_number -> [(date, is_1st)]
    try:
        hist_cur = src.execute("""
            SELECT e.racer_number, r.race_date, res.finishing_position
            FROM race_entries e
            JOIN races r ON e.race_id=r.race_id
            JOIN race_results res ON res.race_id=e.race_id AND res.boat_number=1
            WHERE e.boat_number=1 AND res.finishing_position IS NOT NULL
            ORDER BY e.racer_number, r.race_date
        """)
        for racer, rd, pos in hist_cur.fetchall():
            course1_hist.setdefault(racer, []).append((str(rd), 1 if pos == 1 else 0))
    except Exception as e:
        print(f"  course1 history skip: {e}")

    def _is_1c80(racer, race_date):
        rh = course1_hist.get(racer, [])
        if not rh: return False
        try:
            rd = _dt.date.fromisoformat(str(race_date))
            cutoff = (rd - _dt.timedelta(days=180)).isoformat()
            past = [w for d, w in rh if cutoff <= d < str(race_date)]
        except Exception:
            return False
        if len(past) < 20: return False
        return (sum(past) / len(past)) >= 0.80

    def _is_l4_pro(avg_st, age, ex_st):
        try:
            if avg_st is None or age is None: return False
            ast, a = float(avg_st), int(age)
            if ast >= 0.16 or not (30 <= a <= 49): return False
            if ex_st is None: return True
            return float(ex_st) < 0.18
        except Exception:
            return False

    by_date: dict[str, dict] = {}
    for row in cur.fetchall():
        (rdate, rid, grade, race_no, stadium_no, racer, avg_st, age, natl_1, ex_st,
         fav_pay, fav_odds, any_in_l4, w1, w2, w3, wp, ep, tp, boat2_top2) = row
        d = by_date.setdefault(rdate, {
            "date": rdate,
            "n_total": n_total_by_date.get(rdate, 0),
            "n_l4": 0,
            "win_bets": 0, "win_hits": 0, "win_pay": 0,
            "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
            "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
            # サブカテゴリ集計 (3 連単 1-2-3 のみ)
            "c80_bets": 0, "c80_hits": 0, "c80_pay": 0,
            "pro_bets": 0, "pro_hits": 0, "pro_pay": 0,
            "sgg12_bets": 0, "sgg12_hits": 0, "sgg12_pay": 0,
            # 一般戦観察集計 + F1 採用集計
            "gen_tri_bets": 0, "gen_tri_hits": 0, "gen_tri_pay": 0,
            "gen_plus_tri_bets": 0, "gen_plus_tri_hits": 0, "gen_plus_tri_pay": 0,
            "gen_f1_tri_bets": 0, "gen_f1_tri_hits": 0, "gen_f1_tri_pay": 0,
            # L4-prime / L4-12R / 一般戦×12R 観察集計 (3 ヶ月実績で採用判断)
            "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
            "r12_tri_bets": 0,   "r12_tri_hits": 0,   "r12_tri_pay": 0,
            "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
            # 戸田 7R 企画レース観察 (2026-05-19 追加)
            "toda_7r_tri_bets": 0, "toda_7r_tri_hits": 0, "toda_7r_tri_pay": 0,
            # L4-Mid + 1-3-2 観察 (2026-05-19): オッズ 10-20倍帯
            "mid_132_tri_bets": 0, "mid_132_tri_hits": 0, "mid_132_tri_pay": 0,
            # L4-Mid Tier A: 3号艇国1%≥7 絞り (ROI 175.5%, Tier 1)
            "mid_132_tier_a_tri_bets": 0, "mid_132_tier_a_tri_hits": 0, "mid_132_tier_a_tri_pay": 0,
        })
        is_done = (w1 is not None and w2 is not None and w3 is not None)
        tri_hit = is_done and (w1 == 1 and w2 == 2 and w3 == 3)
        tri_pay_v = (tp or 0) if tri_hit else 0
        # race_no を int に変換 (None ガード)
        try:
            rn = int(race_no) if race_no is not None else 0
        except (TypeError, ValueError):
            rn = 0
        is_prime = rn in (11, 12)  # L4-prime 観察用
        is_r12   = rn == 12        # L4-12R / 一般戦×12R 観察用

        # === L4-prime / L4-12R 観察集計 (全 grade、確定済のみ) ===
        # 3 ヶ月実績で採用判断する。L4 universe 全体 (一般戦含む) 11-12R / 12R 限定
        if is_done:
            if is_prime:
                d["prime_tri_bets"] += 1
                if tri_hit:
                    d["prime_tri_hits"] += 1
                    d["prime_tri_pay"] += tri_pay_v
            if is_r12:
                d["r12_tri_bets"] += 1
                if tri_hit:
                    d["r12_tri_hits"] += 1
                    d["r12_tri_pay"] += tri_pay_v
        # === 一般戦 (grade=5): L4 本流と分離して観察集計 + F1 採用集計 ===
        if grade == 5:
            if is_done:
                d["gen_tri_bets"] += 1
                if tri_hit:
                    d["gen_tri_hits"] += 1
                    d["gen_tri_pay"] += tri_pay_v
                # 一般戦×12R 観察 (F1 と独立の観察ベース)
                if is_r12:
                    d["gen_r12_tri_bets"] += 1
                    if tri_hit:
                        d["gen_r12_tri_hits"] += 1
                        d["gen_r12_tri_pay"] += tri_pay_v
                try:
                    n1 = float(natl_1) if natl_1 is not None else 0.0
                except (TypeError, ValueError):
                    n1 = 0.0
                try:
                    b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                except (TypeError, ValueError):
                    b2 = 0.0
                # 一般戦 × 国1%≥7 (L4+ オーバーレイ) — 観察
                if n1 >= 7.0:
                    d["gen_plus_tri_bets"] += 1
                    if tri_hit:
                        d["gen_plus_tri_hits"] += 1
                        d["gen_plus_tri_pay"] += tri_pay_v
                # ★F1 採用: 国1%≥7 + 2号 top_2≥40
                # F1 は本日候補リスト・メール通知の対象 (= 採用ベース) なので、
                # n_l4 / tri_bets / win_bets / exa_bets にも加算する。
                # これは _l4_daily_stats (app.py L2103-2126) と同じ整合性を保つ。
                # backlog item 19: 旧実装は F1 を gen_f1_tri_* にしか入れず、
                # 古い日の Render 表示が summary 値と乖離 (5/14 など F1 ROI が消失)
                if n1 >= 7.0 and b2 >= 40.0:
                    d["gen_f1_tri_bets"] += 1
                    if tri_hit:
                        d["gen_f1_tri_hits"] += 1
                        d["gen_f1_tri_pay"] += tri_pay_v
                    # ★ F1 は採用カテゴリ → メイン集計にも統合
                    d["n_l4"] += 1
                    d["win_bets"] += 1
                    d["exa_bets"] += 1
                    d["tri_bets"] += 1
                    if w1 == 1:
                        d["win_hits"] += 1
                        d["win_pay"] += (wp or 0)
                    if w1 == 1 and w2 == 2:
                        d["exa_hits"] += 1
                        d["exa_pay"] += (ep or 0)
                    if tri_hit:
                        d["tri_hits"] += 1
                        d["tri_pay"] += tri_pay_v
            continue  # 一般戦の他 (非F1) は L4 本流集計に含めない

        # === L4 本流 (grade IN 1,2,3,4) ===
        d["n_l4"] += 1
        # 確定済 (w1/w2/w3 揃ってる) のみ bets/hits/pay を加算
        if is_done:
            d["win_bets"] += 1
            d["exa_bets"] += 1
            d["tri_bets"] += 1
            if w1 == 1:
                d["win_hits"] += 1
                d["win_pay"] += (wp or 0)
            if w1 == 1 and w2 == 2:
                d["exa_hits"] += 1
                d["exa_pay"] += (ep or 0)
            if tri_hit:
                d["tri_hits"] += 1
                d["tri_pay"] += (tp or 0)
            # ▼ サブカテゴリ判定 (確定済のみ集計)
            # 1c80 (1コース 1着率 80%+)
            if _is_1c80(racer, rdate):
                d["c80_bets"] += 1
                if tri_hit:
                    d["c80_hits"] += 1
                    d["c80_pay"] += (tp or 0)
            # L4 PRO
            if _is_l4_pro(avg_st, age, ex_st):
                d["pro_bets"] += 1
                if tri_hit:
                    d["pro_hits"] += 1
                    d["pro_pay"] += (tp or 0)
            # SG/G1/G2 (高グレード)
            if grade in (1, 2, 3):
                d["sgg12_bets"] += 1
                if tri_hit:
                    d["sgg12_hits"] += 1
                    d["sgg12_pay"] += (tp or 0)
    # === 戸田 7R 観察 (B除外内、別パスで集計) ===
    # 通常 SQL は B除外 (戸田 stadium=2) を pre-filter で弾くため、ここで補完。
    # backtest で ROI 171.5% (n=106) と検証済。3 ヶ月実績で採用判断。
    sql_toda = """
        SELECT r.race_date, r.race_id,
               oo.any_in_l4 AS any_in_l4,
               pp.min_pay AS fav_pay, oo.min_odds AS fav_odds,
               res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
               pt.payout AS tri_pay
        FROM races r
        JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
        LEFT JOIN (SELECT race_id,
                          MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4,
                          MIN(odds) AS min_odds
                   FROM odds_trifecta
                   WHERE combination='1-2-3' AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                   GROUP BY race_id) oo ON oo.race_id=r.race_id
        LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
        WHERE r.race_date BETWEEN ? AND ?
          AND r.stadium_number = 2 AND r.race_number = 7
          AND e.class_number = 1
          AND (pv.weather_number IS NULL OR pv.weather_number != 3)
          AND (
              oo.any_in_l4 = 1
              OR (oo.any_in_l4 IS NULL AND pp.min_pay BETWEEN 500 AND 999)
          )
    """
    # === L4-Mid 1-3-2 観察 (2026-05-19): オッズ 10-20倍帯、別 universe ===
    # 検証 ROI 148.1% (n=10,690), B除外 + A1, 1-3-2 単点
    sql_mid = f"""
        SELECT r.race_date, r.race_id,
               oo.any_in_l4_mid AS any_in_l4_mid,
               pp.min_pay AS fav_pay,
               res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
               pt_132.payout AS pay_132,
               e3.national_top_1_percent AS boat3_natl_1
        FROM races r
        JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
        LEFT JOIN race_entries e3 ON e3.race_id=r.race_id AND e3.boat_number=3
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
        LEFT JOIN (SELECT race_id,
                          MAX(CASE WHEN odds >= 10 AND odds < 20 THEN 1 ELSE 0 END) AS any_in_l4_mid
                   FROM odds_trifecta
                   WHERE combination='1-2-3' AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                   GROUP BY race_id) oo ON oo.race_id=r.race_id
        LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pt_132 ON pt_132.race_id=r.race_id AND pt_132.bet_type='trifecta' AND pt_132.combination='1-3-2'
        WHERE r.race_date BETWEEN ? AND ?
          AND e.class_number = 1
          AND r.stadium_number NOT IN ({placeholders})
          AND (pv.weather_number IS NULL OR pv.weather_number != 3)
          AND (
              oo.any_in_l4_mid = 1
              OR (oo.any_in_l4_mid IS NULL AND pp.min_pay BETWEEN 1000 AND 1999)
          )
    """
    for row in src.execute(sql_mid, (start, end, *EXCLUDE_B)).fetchall():
        rdate, rid, any_mid, fav_pay, w1, w2, w3, p132, boat3_natl_1 = row
        if w1 is None or w2 is None or w3 is None: continue
        hit_132 = (w1==1 and w2==3 and w3==2)
        p132_v = (p132 or 0) if hit_132 else 0
        d = by_date.setdefault(rdate, {
            "date": rdate,
            "n_total": n_total_by_date.get(rdate, 0),
            "n_l4": 0,
            "win_bets": 0, "win_hits": 0, "win_pay": 0,
            "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
            "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
            "c80_bets": 0, "c80_hits": 0, "c80_pay": 0,
            "pro_bets": 0, "pro_hits": 0, "pro_pay": 0,
            "sgg12_bets": 0, "sgg12_hits": 0, "sgg12_pay": 0,
            "gen_tri_bets": 0, "gen_tri_hits": 0, "gen_tri_pay": 0,
            "gen_plus_tri_bets": 0, "gen_plus_tri_hits": 0, "gen_plus_tri_pay": 0,
            "gen_f1_tri_bets": 0, "gen_f1_tri_hits": 0, "gen_f1_tri_pay": 0,
            "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
            "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
            "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
            "toda_7r_tri_bets": 0, "toda_7r_tri_hits": 0, "toda_7r_tri_pay": 0,
            "mid_132_tri_bets": 0, "mid_132_tri_hits": 0, "mid_132_tri_pay": 0,
            "mid_132_tier_a_tri_bets": 0, "mid_132_tier_a_tri_hits": 0, "mid_132_tier_a_tri_pay": 0,
        })
        d["mid_132_tri_bets"] += 1
        if hit_132:
            d["mid_132_tri_hits"] += 1
            d["mid_132_tri_pay"] += p132_v
        # Tier A: 3号艇 国1% ≥ 7
        try:
            b3_n1 = float(boat3_natl_1) if boat3_natl_1 is not None else 0.0
        except (TypeError, ValueError):
            b3_n1 = 0.0
        if b3_n1 >= 7.0:
            d["mid_132_tier_a_tri_bets"] += 1
            if hit_132:
                d["mid_132_tier_a_tri_hits"] += 1
                d["mid_132_tier_a_tri_pay"] += p132_v

    # === 戸田 7R 観察 (B除外内、別パス) ===
    for row in src.execute(sql_toda, (start, end)).fetchall():
        rdate, rid, any_l4, fav_pay, fav_odds, w1, w2, w3, tp = row
        if w1 is None or w2 is None or w3 is None: continue
        tri_hit = (w1==1 and w2==2 and w3==3)
        tp_v = (tp or 0) if tri_hit else 0
        # 戸田7R 単独でその日に L4 メイン候補が無い場合のため、setdefault でテンプレ補完
        d = by_date.setdefault(rdate, {
            "date": rdate,
            "n_total": n_total_by_date.get(rdate, 0),
            "n_l4": 0,
            "win_bets": 0, "win_hits": 0, "win_pay": 0,
            "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
            "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
            "c80_bets": 0, "c80_hits": 0, "c80_pay": 0,
            "pro_bets": 0, "pro_hits": 0, "pro_pay": 0,
            "sgg12_bets": 0, "sgg12_hits": 0, "sgg12_pay": 0,
            "gen_tri_bets": 0, "gen_tri_hits": 0, "gen_tri_pay": 0,
            "gen_plus_tri_bets": 0, "gen_plus_tri_hits": 0, "gen_plus_tri_pay": 0,
            "gen_f1_tri_bets": 0, "gen_f1_tri_hits": 0, "gen_f1_tri_pay": 0,
            "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
            "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
            "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
            "toda_7r_tri_bets": 0, "toda_7r_tri_hits": 0, "toda_7r_tri_pay": 0,
        })
        d["toda_7r_tri_bets"] += 1
        if tri_hit:
            d["toda_7r_tri_hits"] += 1
            d["toda_7r_tri_pay"] += tp_v

    return list(by_date.values())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=str, default="2022-01-01")
    p.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--src", choices=["supabase", "local"], default="supabase",
                   help="集計データ源 (default=supabase)")
    p.add_argument("--recent-days", type=int, default=None,
                   help="直近 N 日のみ集計 (--start/--end をオーバーライド)。"
                        "run_daily_collect.bat や run_hourly_task.bat から日次/時間別の"
                        "増分同期に使用 (backlog item 19)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    # --recent-days 指定時は --start/--end をオーバーライド
    if args.recent_days is not None and args.recent_days > 0:
        from datetime import timedelta as _td
        today_dt = datetime.now().date()
        args.end = today_dt.strftime("%Y-%m-%d")
        args.start = (today_dt - _td(days=args.recent_days - 1)).strftime("%Y-%m-%d")

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL が Supabase URL に設定されていません (.env 確認)")
        sys.exit(1)

    print(f"=== L4 [A1] 集計のみ同期: {args.start} ~ {args.end} (src={args.src}) ===")
    print()

    # データ源
    if args.src == "supabase":
        # Supabase から計算 (DATABASE_URL を尊重)
        src = db_connect()
    else:
        # ローカル SQLite から計算 (2022-2024 等、Supabase に無い期間)
        src = sqlite3.connect(config.DB_PATH)
    summaries = compute_summary(src, args.start, args.end)
    try:
        src.close()
    except Exception:
        pass
    print(f"  集計対象日数: {len(summaries)}")
    n_l4_total = sum(s["n_l4"] for s in summaries)
    tri_pay_total = sum(s["tri_pay"] for s in summaries)
    tri_bets_total = sum(s["tri_bets"] for s in summaries)
    if tri_bets_total:
        print(f"  L4 通算: {n_l4_total} 件, 3連単 1-2-3 損益 {tri_pay_total - 100*tri_bets_total:+,}円")

    # Supabase に upsert
    dst = db_connect()
    now_iso = datetime.now().isoformat(timespec="seconds")
    n_upsert = 0
    BATCH = 500
    batch = []
    # UPSERT: Postgres は ON CONFLICT、SQLite は INSERT OR REPLACE 構文。
    # db_connect() が返す接続種別で SQL を切替える。
    is_pg = db_url.startswith(("postgres://", "postgresql://"))
    if is_pg:
        UPSERT_SQL = """
            INSERT INTO l4_daily_summary
              (date, n_total, n_l4,
               win_bets, win_hits, win_pay,
               exa_bets, exa_hits, exa_pay,
               tri_bets, tri_hits, tri_pay,
               c80_bets, c80_hits, c80_pay,
               pro_bets, pro_hits, pro_pay,
               sgg12_bets, sgg12_hits, sgg12_pay,
               gen_tri_bets, gen_tri_hits, gen_tri_pay,
               gen_plus_tri_bets, gen_plus_tri_hits, gen_plus_tri_pay,
               gen_f1_tri_bets, gen_f1_tri_hits, gen_f1_tri_pay,
               prime_tri_bets, prime_tri_hits, prime_tri_pay,
               r12_tri_bets, r12_tri_hits, r12_tri_pay,
               gen_r12_tri_bets, gen_r12_tri_hits, gen_r12_tri_pay,
               toda_7r_tri_bets, toda_7r_tri_hits, toda_7r_tri_pay,
               mid_132_tri_bets, mid_132_tri_hits, mid_132_tri_pay,
               mid_132_tier_a_tri_bets, mid_132_tier_a_tri_hits, mid_132_tier_a_tri_pay,
               updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (date) DO UPDATE SET
              n_total = EXCLUDED.n_total,
              n_l4    = EXCLUDED.n_l4,
              win_bets = EXCLUDED.win_bets, win_hits = EXCLUDED.win_hits, win_pay = EXCLUDED.win_pay,
              exa_bets = EXCLUDED.exa_bets, exa_hits = EXCLUDED.exa_hits, exa_pay = EXCLUDED.exa_pay,
              tri_bets = EXCLUDED.tri_bets, tri_hits = EXCLUDED.tri_hits, tri_pay = EXCLUDED.tri_pay,
              c80_bets = EXCLUDED.c80_bets, c80_hits = EXCLUDED.c80_hits, c80_pay = EXCLUDED.c80_pay,
              pro_bets = EXCLUDED.pro_bets, pro_hits = EXCLUDED.pro_hits, pro_pay = EXCLUDED.pro_pay,
              sgg12_bets = EXCLUDED.sgg12_bets, sgg12_hits = EXCLUDED.sgg12_hits, sgg12_pay = EXCLUDED.sgg12_pay,
              gen_tri_bets = EXCLUDED.gen_tri_bets,
              gen_tri_hits = EXCLUDED.gen_tri_hits,
              gen_tri_pay  = EXCLUDED.gen_tri_pay,
              gen_plus_tri_bets = EXCLUDED.gen_plus_tri_bets,
              gen_plus_tri_hits = EXCLUDED.gen_plus_tri_hits,
              gen_plus_tri_pay  = EXCLUDED.gen_plus_tri_pay,
              gen_f1_tri_bets = EXCLUDED.gen_f1_tri_bets,
              gen_f1_tri_hits = EXCLUDED.gen_f1_tri_hits,
              gen_f1_tri_pay  = EXCLUDED.gen_f1_tri_pay,
              prime_tri_bets = EXCLUDED.prime_tri_bets,
              prime_tri_hits = EXCLUDED.prime_tri_hits,
              prime_tri_pay  = EXCLUDED.prime_tri_pay,
              r12_tri_bets   = EXCLUDED.r12_tri_bets,
              r12_tri_hits   = EXCLUDED.r12_tri_hits,
              r12_tri_pay    = EXCLUDED.r12_tri_pay,
              gen_r12_tri_bets = EXCLUDED.gen_r12_tri_bets,
              gen_r12_tri_hits = EXCLUDED.gen_r12_tri_hits,
              gen_r12_tri_pay  = EXCLUDED.gen_r12_tri_pay,
              toda_7r_tri_bets = EXCLUDED.toda_7r_tri_bets,
              toda_7r_tri_hits = EXCLUDED.toda_7r_tri_hits,
              toda_7r_tri_pay  = EXCLUDED.toda_7r_tri_pay,
              mid_132_tri_bets = EXCLUDED.mid_132_tri_bets,
              mid_132_tri_hits = EXCLUDED.mid_132_tri_hits,
              mid_132_tri_pay  = EXCLUDED.mid_132_tri_pay,
              mid_132_tier_a_tri_bets = EXCLUDED.mid_132_tier_a_tri_bets,
              mid_132_tier_a_tri_hits = EXCLUDED.mid_132_tier_a_tri_hits,
              mid_132_tier_a_tri_pay  = EXCLUDED.mid_132_tier_a_tri_pay,
              updated_at = EXCLUDED.updated_at
        """
    else:
        UPSERT_SQL = """
            INSERT OR REPLACE INTO l4_daily_summary
              (date, n_total, n_l4,
               win_bets, win_hits, win_pay,
               exa_bets, exa_hits, exa_pay,
               tri_bets, tri_hits, tri_pay,
               c80_bets, c80_hits, c80_pay,
               pro_bets, pro_hits, pro_pay,
               sgg12_bets, sgg12_hits, sgg12_pay,
               gen_tri_bets, gen_tri_hits, gen_tri_pay,
               gen_plus_tri_bets, gen_plus_tri_hits, gen_plus_tri_pay,
               gen_f1_tri_bets, gen_f1_tri_hits, gen_f1_tri_pay,
               prime_tri_bets, prime_tri_hits, prime_tri_pay,
               r12_tri_bets, r12_tri_hits, r12_tri_pay,
               gen_r12_tri_bets, gen_r12_tri_hits, gen_r12_tri_pay,
               toda_7r_tri_bets, toda_7r_tri_hits, toda_7r_tri_pay,
               mid_132_tri_bets, mid_132_tri_hits, mid_132_tri_pay,
               mid_132_tier_a_tri_bets, mid_132_tier_a_tri_hits, mid_132_tier_a_tri_pay,
               updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    for s in summaries:
        batch.append((
            s["date"], s["n_total"], s["n_l4"],
            s["win_bets"], s["win_hits"], s["win_pay"],
            s["exa_bets"], s["exa_hits"], s["exa_pay"],
            s["tri_bets"], s["tri_hits"], s["tri_pay"],
            s.get("c80_bets",0), s.get("c80_hits",0), s.get("c80_pay",0),
            s.get("pro_bets",0), s.get("pro_hits",0), s.get("pro_pay",0),
            s.get("sgg12_bets",0), s.get("sgg12_hits",0), s.get("sgg12_pay",0),
            s.get("gen_tri_bets",0), s.get("gen_tri_hits",0), s.get("gen_tri_pay",0),
            s.get("gen_plus_tri_bets",0), s.get("gen_plus_tri_hits",0), s.get("gen_plus_tri_pay",0),
            s.get("gen_f1_tri_bets",0), s.get("gen_f1_tri_hits",0), s.get("gen_f1_tri_pay",0),
            s.get("prime_tri_bets",0), s.get("prime_tri_hits",0), s.get("prime_tri_pay",0),
            s.get("r12_tri_bets",0),   s.get("r12_tri_hits",0),   s.get("r12_tri_pay",0),
            s.get("gen_r12_tri_bets",0), s.get("gen_r12_tri_hits",0), s.get("gen_r12_tri_pay",0),
            s.get("toda_7r_tri_bets",0), s.get("toda_7r_tri_hits",0), s.get("toda_7r_tri_pay",0),
            s.get("mid_132_tri_bets",0), s.get("mid_132_tri_hits",0), s.get("mid_132_tri_pay",0),
            s.get("mid_132_tier_a_tri_bets",0), s.get("mid_132_tier_a_tri_hits",0), s.get("mid_132_tier_a_tri_pay",0),
            now_iso,
        ))
        if len(batch) >= BATCH:
            dst.executemany(UPSERT_SQL, batch)
            n_upsert += len(batch)
            batch.clear()
            if args.verbose:
                print(f"  upserted {n_upsert} rows")
    if batch:
        dst.executemany(UPSERT_SQL, batch)
        n_upsert += len(batch)
    dst.commit()
    dst.close()
    print(f"\n  Supabase へ {n_upsert} 日分の集計を upsert 完了")


if __name__ == "__main__":
    main()
