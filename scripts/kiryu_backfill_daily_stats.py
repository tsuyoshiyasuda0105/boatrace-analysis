"""桐生 K1/K2/K1_PRIME/K2_PRIME 戦略の過去全レース backfill 集計.

src.evaluation.kiryu_strategy の判定ロジックを過去全 桐生レース に適用し、
月別/年別の bet 数・hit 数・払戻・ROI を集計、CSV と markdown レポートに出力。

実装意図:
  - 既存 _evaluate_l4 が race ループ内で base[...] dict を作るのと同じ形で、
    各レースの K1/K2/K1_PRIME/K2_PRIME 判定 → bets を計算
  - bet ごとに race_payouts から実払戻を取得し ROI を算出
  - 月別/年別/全期間 で集計

実行:
  py -3 scripts/kiryu_backfill_daily_stats.py
出力:
  reports/kiryu_strategy_daily_stats.md       — 月別/年別 ROI 表
  data/kiryu_strategy_daily_stats.csv         — 全レース 1 行/race の詳細
  data/kiryu_strategy_monthly_stats.csv       — 月別集計
  reports/kiryu_backfill.log                  — 実行ログ
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.evaluation import kiryu_strategy as ks
from src.verification.backtest import _conn

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REPORTS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

MD_PATH = REPORTS_DIR / "kiryu_strategy_daily_stats.md"
RACE_CSV_PATH = DATA_DIR / "kiryu_strategy_daily_stats.csv"
MONTHLY_CSV_PATH = DATA_DIR / "kiryu_strategy_monthly_stats.csv"
LOG_PATH = REPORTS_DIR / "kiryu_backfill.log"


def fetch_kiryu_races(conn) -> list[dict]:
    """桐生 (stadium=1) の全レースを 1 行 / race で取得.

    1号艇エントリー (e1) と 4号艇 (e4) と 5号艇 (e5) のクラス・国1・motor、
    race_previews (pv, boat=1) の weather / wind を結合。
    """
    sql = """
SELECT
    r.race_id,
    r.race_date,
    r.race_number,
    r.stadium_number,
    e1.class_number AS boat1_class,
    e1.assigned_motor_top_2_percent AS boat1_motor_top_2,
    e1.national_top_1_percent AS boat1_natl_1,
    e4.class_number AS boat4_class,
    e5.assigned_motor_top_2_percent AS boat5_motor_top_2,
    pv.weather_number,
    pv.wind_direction_number
FROM races r
LEFT JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
LEFT JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
LEFT JOIN race_entries e5 ON e5.race_id = r.race_id AND e5.boat_number = 5
LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
WHERE r.stadium_number = 1
ORDER BY r.race_date, r.race_number
"""
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_payouts_for_race(conn, race_id: str, combos: list[str]) -> dict[str, int]:
    """指定 race_id に対する 3連単 払戻 (combination 別) を取得.

    Returns: {combination: payout_yen} (hit していない combo は dict に含まれない)
    """
    if not combos:
        return {}
    ph = "%s" if os.environ.get("DATABASE_URL") else "?"
    placeholders = ",".join([ph] * len(combos))
    sql = f"""
SELECT combination, payout
FROM race_payouts
WHERE race_id = {ph} AND bet_type = 'trifecta'
  AND combination IN ({placeholders})
"""
    cur = conn.cursor()
    cur.execute(sql, [race_id, *combos])
    return {combo: int(payout) for combo, payout in cur.fetchall() if payout}


def evaluate_race(row: dict) -> dict:
    """1 race に対し K1/K2/K1_PRIME/K2_PRIME を判定し、 bets/labels を返す."""
    # legacy K1/K2
    legacy = ks.evaluate_kiryu_race(
        stadium_number=row["stadium_number"],
        boat1_class=row["boat1_class"],
        boat1_motor_top_2_percent=row["boat1_motor_top_2"],
        boat1_national_top_1_percent=row["boat1_natl_1"],
        weather_number=row["weather_number"],
        wind_direction_number=row["wind_direction_number"],
    )
    # refined K1_PRIME / K2_PRIME
    prime = ks.evaluate_kiryu_race_prime(
        stadium_number=row["stadium_number"],
        boat1_class=row["boat1_class"],
        boat1_motor_top_2_percent=row["boat1_motor_top_2"],
        boat1_national_top_1_percent=row["boat1_natl_1"],
        weather_number=row["weather_number"],
        wind_direction_number=row["wind_direction_number"],
        boat4_class=row["boat4_class"],
        boat5_motor_top_2_percent=row["boat5_motor_top_2"],
    )
    return {
        "k1_eligible": legacy["k1_eligible"],
        "k2_eligible": legacy["k2_eligible"],
        "k1_prime_eligible": prime["k1_prime_eligible"],
        "k2_prime_eligible": prime["k2_prime_eligible"],
    }


def main() -> None:
    log_lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        log_lines.append(msg)

    log(f"=== 桐生 K1/K2/K1_PRIME/K2_PRIME 過去レース backfill ===")
    log(f"DB 接続: {'Supabase' if os.environ.get('DATABASE_URL') else 'SQLite ローカル'}")

    conn = _conn()
    races = fetch_kiryu_races(conn)
    log(f"桐生 全レース数: {len(races)}")

    # 各 race の K1/K2/K1_PRIME/K2_PRIME 判定 + 払戻 取得
    race_rows: list[dict] = []
    # 月別/年別 集計用
    # key: (period_key, strategy_key), strategy_key in {K1, K2, K1_PRIME, K2_PRIME}
    period_agg: dict[tuple, dict] = defaultdict(
        lambda: {"bets": 0, "hits": 0, "pay": 0}
    )
    overall_agg: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "hits": 0, "pay": 0}
    )

    # 戦略 → 買い目 combo の対応
    STRATEGY_COMBOS: dict[str, list[str]] = {
        "K1": ["5-1-2", "4-5-2"],          # K1 portfolio 併買
        "K2": ["5-1-2"],
        "K1_PRIME": ["4-5-2"],
        "K2_PRIME": ["5-1-2"],
    }

    for i, row in enumerate(races, 1):
        if i % 500 == 0:
            log(f"  処理中: {i}/{len(races)} races...")

        decision = evaluate_race(row)
        # 必要な combo の払戻を一度に取得
        needed_combos: set[str] = set()
        for strat_key, eligible_key in [
            ("K1", "k1_eligible"),
            ("K2", "k2_eligible"),
            ("K1_PRIME", "k1_prime_eligible"),
            ("K2_PRIME", "k2_prime_eligible"),
        ]:
            if decision[eligible_key]:
                needed_combos.update(STRATEGY_COMBOS[strat_key])
        payouts: dict[str, int] = (
            fetch_payouts_for_race(conn, row["race_id"], list(needed_combos))
            if needed_combos else {}
        )

        # period key: 年-月 (race_date 先頭 7 文字)
        date_str = str(row["race_date"])
        ym = date_str[:7] if len(date_str) >= 7 else "unknown"
        year = date_str[:4] if len(date_str) >= 4 else "unknown"

        # 1 race ごとに各戦略の bets/hits/pay を加算
        race_summary = {
            "race_id": row["race_id"],
            "race_date": date_str,
            "race_number": row["race_number"],
        }
        for strat_key, eligible_key in [
            ("K1", "k1_eligible"),
            ("K2", "k2_eligible"),
            ("K1_PRIME", "k1_prime_eligible"),
            ("K2_PRIME", "k2_prime_eligible"),
        ]:
            race_summary[f"{strat_key}_eligible"] = int(decision[eligible_key])
            if not decision[eligible_key]:
                race_summary[f"{strat_key}_bets"] = 0
                race_summary[f"{strat_key}_hits"] = 0
                race_summary[f"{strat_key}_pay"] = 0
                continue
            combos = STRATEGY_COMBOS[strat_key]
            bets = len(combos) * 100  # 1 combo = 100 円
            pay = sum(payouts.get(c, 0) for c in combos)
            hits = sum(1 for c in combos if c in payouts)
            race_summary[f"{strat_key}_bets"] = bets
            race_summary[f"{strat_key}_hits"] = hits
            race_summary[f"{strat_key}_pay"] = pay

            # 集計に加算
            period_agg[(ym, strat_key)]["bets"] += bets
            period_agg[(ym, strat_key)]["hits"] += hits
            period_agg[(ym, strat_key)]["pay"] += pay
            period_agg[(year, strat_key)]["bets"] += bets
            period_agg[(year, strat_key)]["hits"] += hits
            period_agg[(year, strat_key)]["pay"] += pay
            overall_agg[strat_key]["bets"] += bets
            overall_agg[strat_key]["hits"] += hits
            overall_agg[strat_key]["pay"] += pay

        race_rows.append(race_summary)

    conn.close()
    log(f"処理完了: {len(race_rows)} races")

    # === CSV 出力 (1 race 1 row) ===
    log(f"\n=== CSV 出力 ===")
    fieldnames = [
        "race_id", "race_date", "race_number",
        "K1_eligible", "K1_bets", "K1_hits", "K1_pay",
        "K2_eligible", "K2_bets", "K2_hits", "K2_pay",
        "K1_PRIME_eligible", "K1_PRIME_bets", "K1_PRIME_hits", "K1_PRIME_pay",
        "K2_PRIME_eligible", "K2_PRIME_bets", "K2_PRIME_hits", "K2_PRIME_pay",
    ]
    with open(RACE_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in race_rows:
            w.writerow(row)
    log(f"  race-level CSV: {RACE_CSV_PATH} ({len(race_rows)} rows)")

    # 月別 CSV
    monthly_fieldnames = ["period", "strategy", "bets", "hits", "pay", "hit_rate_pct", "roi_pct"]
    monthly_rows: list[dict] = []
    for (period, strat), agg in sorted(period_agg.items()):
        if agg["bets"] == 0:
            continue
        race_count = agg["bets"] // 100 // len(STRATEGY_COMBOS[strat])
        hit_rate = (agg["hits"] / max(1, race_count * len(STRATEGY_COMBOS[strat]))) * 100
        roi = (agg["pay"] / max(1, agg["bets"])) * 100
        monthly_rows.append({
            "period": period,
            "strategy": strat,
            "bets": agg["bets"],
            "hits": agg["hits"],
            "pay": agg["pay"],
            "hit_rate_pct": round(hit_rate, 2),
            "roi_pct": round(roi, 2),
        })
    with open(MONTHLY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=monthly_fieldnames)
        w.writeheader()
        for row in monthly_rows:
            w.writerow(row)
    log(f"  monthly CSV: {MONTHLY_CSV_PATH} ({len(monthly_rows)} rows)")

    # === Markdown レポート出力 ===
    log(f"\n=== Markdown 出力 ===")
    md_lines: list[str] = []
    md_lines.append("# 桐生 K1/K2/K1_PRIME/K2_PRIME 過去全レース backfill ROI 集計")
    md_lines.append("")
    md_lines.append(f"**生成日**: backfill 実行時")
    md_lines.append(f"**対象**: 桐生 (stadium=1) 全レース ({len(races)} 件)")
    md_lines.append(f"**判定**: `src.evaluation.kiryu_strategy.evaluate_kiryu_race` / `evaluate_kiryu_race_prime`")
    md_lines.append("")
    md_lines.append("## 戦略一覧")
    md_lines.append("")
    md_lines.append("| 戦略 | 条件 | 買い目 |")
    md_lines.append("|---|---|---|")
    md_lines.append("| **K1** | 1号艇 A1 ∧ motor≥35 ∧ 国1≥6 ∧ 雨除外 | 3連単 5-1-2 + 4-5-2 (各100円) |")
    md_lines.append("| **K2** | K1 + 風向 ≠ 6 | 3連単 5-1-2 (100円) |")
    md_lines.append("| **K1_PRIME** | K1 + 4号艇 class=A1 | 3連単 4-5-2 (100円) |")
    md_lines.append("| **K2_PRIME** | K2 + 5号艇 motor≥35 | 3連単 5-1-2 (100円) |")
    md_lines.append("")

    # 全期間 サマリ
    md_lines.append("## 全期間サマリ")
    md_lines.append("")
    md_lines.append("| 戦略 | bets (円) | hits | hit_rate | 払戻 (円) | ROI | 損益 (円) |")
    md_lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for strat in ["K1", "K2", "K1_PRIME", "K2_PRIME"]:
        agg = overall_agg.get(strat, {"bets": 0, "hits": 0, "pay": 0})
        if agg["bets"] == 0:
            continue
        n_bet_units = agg["bets"] // 100  # 100円単位の bet 数
        hit_rate = (agg["hits"] / max(1, n_bet_units)) * 100
        roi = (agg["pay"] / max(1, agg["bets"])) * 100
        profit = agg["pay"] - agg["bets"]
        md_lines.append(
            f"| {strat} | {agg['bets']:,} | {agg['hits']} | {hit_rate:.2f}% | {agg['pay']:,} | "
            f"{roi:.1f}% | {profit:+,} |"
        )
    md_lines.append("")

    # 年別
    md_lines.append("## 年別 ROI")
    md_lines.append("")
    md_lines.append("| 年 | 戦略 | bets (円) | hits | hit_rate | 払戻 (円) | ROI |")
    md_lines.append("|---|---|---:|---:|---:|---:|---:|")
    years_sorted = sorted({k for k in period_agg.keys() if isinstance(k[0], str) and len(k[0]) == 4})
    for ykey in years_sorted:
        year_str, strat = ykey
        agg = period_agg[ykey]
        if agg["bets"] == 0:
            continue
        n_bet_units = agg["bets"] // 100
        hit_rate = (agg["hits"] / max(1, n_bet_units)) * 100
        roi = (agg["pay"] / max(1, agg["bets"])) * 100
        md_lines.append(
            f"| {year_str} | {strat} | {agg['bets']:,} | {agg['hits']} | {hit_rate:.2f}% | "
            f"{agg['pay']:,} | {roi:.1f}% |"
        )
    md_lines.append("")

    # 月別 (最近 24ヶ月)
    md_lines.append("## 月別 ROI (最近 24 ヶ月)")
    md_lines.append("")
    md_lines.append("| 月 | 戦略 | bets (円) | hits | hit_rate | 払戻 (円) | ROI |")
    md_lines.append("|---|---|---:|---:|---:|---:|---:|")
    months_sorted = sorted({k[0] for k in period_agg.keys() if len(k[0]) == 7})
    recent_months = months_sorted[-24:]
    for ym in recent_months:
        for strat in ["K1", "K2", "K1_PRIME", "K2_PRIME"]:
            agg = period_agg.get((ym, strat))
            if not agg or agg["bets"] == 0:
                continue
            n_bet_units = agg["bets"] // 100
            hit_rate = (agg["hits"] / max(1, n_bet_units)) * 100
            roi = (agg["pay"] / max(1, agg["bets"])) * 100
            md_lines.append(
                f"| {ym} | {strat} | {agg['bets']:,} | {agg['hits']} | {hit_rate:.2f}% | "
                f"{agg['pay']:,} | {roi:.1f}% |"
            )
    md_lines.append("")

    # 直近の発火レース (最新 20 件)
    md_lines.append("## 直近の発火レース (最新 20 件)")
    md_lines.append("")
    md_lines.append("| race_date | race_no | K1 | K2 | K1' | K2' | K1 payout | K2 payout | K1' payout | K2' payout |")
    md_lines.append("|---|---:|:---:|:---:|:---:|:---:|---:|---:|---:|---:|")
    eligible_races = [r for r in race_rows if any(r[f"{s}_eligible"] for s in ["K1", "K2", "K1_PRIME", "K2_PRIME"])]
    for r in eligible_races[-20:]:
        md_lines.append(
            f"| {r['race_date']} | {r['race_number']} | "
            f"{'✅' if r['K1_eligible'] else ''} | "
            f"{'✅' if r['K2_eligible'] else ''} | "
            f"{'✅' if r['K1_PRIME_eligible'] else ''} | "
            f"{'✅' if r['K2_PRIME_eligible'] else ''} | "
            f"{r['K1_pay']:,} | {r['K2_pay']:,} | {r['K1_PRIME_pay']:,} | {r['K2_PRIME_pay']:,} |"
        )
    md_lines.append("")

    # 注意事項
    md_lines.append("## 注意事項")
    md_lines.append("")
    md_lines.append("- **大穴狙い戦略**: 的中率 1-4% / 平均配当 16,000-27,000円。")
    md_lines.append("- 「当たらない月が多い」のが期待挙動。年に数回の的中で +ROI を回収。")
    md_lines.append("- 風向データは 2025-07-15 以降のみ存在 → K2 / K2_PRIME はそれ以前 race では wd=NULL 扱い。")
    md_lines.append("- K2_PRIME / K1_PRIME は 5号艇 motor / 4号艇 class に依存 → 対象 race 数が K1/K2 より少ない。")
    md_lines.append("- 検証スプリット日: 2026-01-01 (それ以前 = train, 以降 = test)。")
    md_lines.append("")
    md_lines.append("## 関連ファイル")
    md_lines.append("")
    md_lines.append(f"- レース別詳細 CSV: `{RACE_CSV_PATH.relative_to(REPORTS_DIR.parent)}`")
    md_lines.append(f"- 月別集計 CSV: `{MONTHLY_CSV_PATH.relative_to(REPORTS_DIR.parent)}`")
    md_lines.append(f"- 戦略定義: `src/evaluation/kiryu_strategy.py`")
    md_lines.append(f"- 検証レポート: `reports/kiryu_wind_boat4.md` / `reports/kiryu_winrate_improvement.md`")

    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    log(f"  markdown report: {MD_PATH}")

    # ログ書出し
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"  log: {LOG_PATH}")

    log("\n完了.")


if __name__ == "__main__":
    main()
