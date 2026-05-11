"""
チルト戦略 × 三連単 絞り込みバックテスト

目的:
  「艇4 tilt 0.5-1.5」「艇5 tilt=3.0」のような戦略で、
  実際に 1着固定 + 2-3着絞り込み による 10通り買いの ROI を計測する。

絞り込みロジック (3パターン比較):
  A. 全20通り (1着固定で X-Y 全組合せ)
  B. 上位10通り (national_top_2_percent 上位の 2艇 + 残り)
  C. モデル予測上位10通り (Cascade で 2-3着確率)

評価:
  - Hit rate (何%のレースで的中)
  - Avg payout (的中時の配当)
  - ROI = (sum_hit_payouts) / (n_races * n_bets * 100) - 1
  - Bootstrap CI
"""
import sqlite3
import random
import statistics
from typing import List, Tuple
from itertools import permutations

DB = "data/boatrace.db"
N_BOOT = 2000
random.seed(42)


def bootstrap_ci_pct(per_race_pct: List[float]) -> dict:
    """per_race_pct: 各レースの (payout / total_bet) リスト
    例えば1レースで10通り×100円=1000円賭けて、当たり1000円なら 1.0"""
    n = len(per_race_pct)
    if n == 0:
        return {"n": 0, "roi": None, "lo": None, "hi": None, "p0": None}
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(per_race_pct, k=n)
        rois.append(sum(sample) / n - 1.0)
    rois.sort()
    return {
        "n": n,
        "roi": sum(per_race_pct) / n - 1.0,
        "lo": rois[int(N_BOOT * 0.025)],
        "hi": rois[int(N_BOOT * 0.975)],
        "p0": sum(1 for r in rois if r > 0) / N_BOOT,
    }


def gen_full_20(fixed_first: int) -> List[str]:
    """1着固定で X-Y の全20通り"""
    others = [b for b in [1, 2, 3, 4, 5, 6] if b != fixed_first]
    return [f"{fixed_first}-{x}-{y}" for x, y in permutations(others, 2)]


def gen_top_k_by_score(fixed_first: int, scores: dict, k: int) -> List[str]:
    """1着固定で、scores 上位 N 艇から X-Y を選び上位 k 通りを返す"""
    others = [b for b in [1, 2, 3, 4, 5, 6] if b != fixed_first]
    # 各艇のスコア (高いほど良い)
    sorted_others = sorted(others, key=lambda b: scores.get(b, 0), reverse=True)
    # 上位3艇から X-Y の組合せを作る (= 3P2 = 6通り) + 上位2 + 残りから (4通り)
    combos = []
    # 上位3艇からの全 6 通り
    for x, y in permutations(sorted_others[:3], 2):
        combos.append(f"{fixed_first}-{x}-{y}")
    # 残りも追加
    for x in sorted_others[:3]:
        for y in sorted_others[3:]:
            combos.append(f"{fixed_first}-{x}-{y}")
            if len(combos) >= k:
                break
        if len(combos) >= k:
            break
    return combos[:k]


def get_strategy_data(conn, where_filter: str, fixed_first: int) -> List[dict]:
    """戦略条件に該当する各レースの (実勝者三連単, 各艇のスコア, 配当) を取得"""
    cur = conn.execute(f"""
        SELECT r.race_id,
               GROUP_CONCAT(e.boat_number || ':' || e.national_top_2_percent, ',') as score_data,
               GROUP_CONCAT(e.boat_number || ':' || e.assigned_motor_top_2_percent, ',') as motor_data,
               GROUP_CONCAT(e.boat_number || ':' || e.class_number, ',') as class_data,
               GROUP_CONCAT(e.boat_number || ':' || COALESCE(p_other.exhibition_time, 999), ',') as ex_data
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {fixed_first}
        LEFT JOIN race_previews p_other ON r.race_id = p_other.race_id AND e.boat_number = p_other.boat_number
        WHERE {where_filter}
        GROUP BY r.race_id
    """)
    races_data = {}
    for race_id, score_data, motor_data, class_data, ex_data in cur.fetchall():
        scores = {}
        motors = {}
        classes = {}
        ex_times = {}
        for entry in (score_data or "").split(","):
            if ":" in entry:
                k, v = entry.split(":")
                scores[int(k)] = float(v or 0)
        for entry in (motor_data or "").split(","):
            if ":" in entry:
                k, v = entry.split(":")
                motors[int(k)] = float(v or 0)
        for entry in (class_data or "").split(","):
            if ":" in entry:
                k, v = entry.split(":")
                classes[int(k)] = int(v or 4)
        for entry in (ex_data or "").split(","):
            if ":" in entry:
                k, v = entry.split(":")
                ex_times[int(k)] = float(v or 999)
        races_data[race_id] = {
            "scores": scores, "motors": motors, "classes": classes, "ex_times": ex_times
        }

    # 三連単結果取得 (where_filter は e.boat_number 等を参照する可能性があるので JOIN を含める)
    cur = conn.execute(f"""
        SELECT pp.race_id, pp.combination, pp.payout
        FROM race_payouts pp
        WHERE pp.bet_type = 'trifecta'
          AND pp.race_id IN (
              SELECT DISTINCT r.race_id FROM races r
              JOIN race_entries e ON r.race_id = e.race_id
              JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {fixed_first}
              WHERE {where_filter}
          )
    """)
    results = []
    for race_id, combo, payout in cur.fetchall():
        if race_id in races_data:
            results.append({
                "race_id": race_id,
                "winning_combo": combo,
                "winning_payout": payout,
                **races_data[race_id]
            })
    return results


def simulate_strategy(races: List[dict], fixed_first: int, strategy: str, n_bets: int) -> dict:
    """戦略をシミュレーションし、各レースの per_race_yield を返す"""
    per_race_yields = []
    n_hits = 0
    hit_payouts = []

    for race in races:
        if strategy == "all_20":
            combos = gen_full_20(fixed_first)
            actual_bets = 20
        elif strategy == "top_by_national":
            # national_top_2_percent でスコアリング
            scores = race["scores"]
            combos = gen_top_k_by_score(fixed_first, scores, n_bets)
            actual_bets = len(combos)
        elif strategy == "top_by_motor":
            scores = race["motors"]
            combos = gen_top_k_by_score(fixed_first, scores, n_bets)
            actual_bets = len(combos)
        elif strategy == "top_by_exhibition":
            # 展示タイム少ない (速い) ほど上位
            ex_times = race["ex_times"]
            scores = {k: -v for k, v in ex_times.items()}  # 反転
            combos = gen_top_k_by_score(fixed_first, scores, n_bets)
            actual_bets = len(combos)
        elif strategy == "top_by_class":
            # 級別: 1=A1が最強 → スコアは (5 - class_number)
            classes = race["classes"]
            scores = {k: (5 - v) for k, v in classes.items()}
            combos = gen_top_k_by_score(fixed_first, scores, n_bets)
            actual_bets = len(combos)
        elif strategy == "top_by_combined":
            # 級別 + national + motor の合成スコア
            national = race["scores"]
            motors = race["motors"]
            classes = race["classes"]
            scores = {k: national.get(k, 0)*0.5 + motors.get(k, 0)*0.3 + (5 - classes.get(k, 4))*5
                      for k in [1, 2, 3, 4, 5, 6] if k != fixed_first}
            combos = gen_top_k_by_score(fixed_first, scores, n_bets)
            actual_bets = len(combos)
        else:
            continue

        total_bet = actual_bets * 100
        payout = 0
        if race["winning_combo"] in combos:
            payout = race["winning_payout"]
            n_hits += 1
            hit_payouts.append(payout)
        per_race_yields.append(payout / total_bet)

    return {
        "n_races": len(races),
        "n_hits": n_hits,
        "hit_rate": n_hits / len(races) if races else 0,
        "avg_hit_payout": statistics.mean(hit_payouts) if hit_payouts else 0,
        "median_hit_payout": statistics.median(hit_payouts) if hit_payouts else 0,
        "per_race_yields": per_race_yields,
    }


def run_strategies(conn, label: str, where: str, fixed_first: int):
    print()
    print("=" * 110)
    print(f"[{label}] 1着={fixed_first}号艇  条件: {where[:80]}")
    print("=" * 110)

    races = get_strategy_data(conn, where, fixed_first)
    if not races:
        print("  (該当レースなし)")
        return

    print(f"  該当レース数: {len(races)}")
    print()
    print(f"  {'戦略':<35} {'n':>5} {'hit':>5} {'hit率':>8} {'avg配当':>10} {'ROI(点)':>10} {'CI':>30} {'P>0':>6}")
    print("  " + "-" * 110)

    for strategy_name, n_bets, display in [
        ("all_20", 20, "全20通り買い"),
        ("top_by_national", 10, "national率上位10通り"),
        ("top_by_motor", 10, "モーター上位10通り"),
        ("top_by_exhibition", 10, "展示上位10通り"),
        ("top_by_class", 10, "級別上位10通り"),
        ("top_by_combined", 10, "総合スコア上位10通り"),
        ("top_by_national", 6, "national率上位 6通り (狭)"),
        ("top_by_combined", 6, "総合スコア上位 6通り (狭)"),
    ]:
        result = simulate_strategy(races, fixed_first, strategy_name, n_bets)
        ci = bootstrap_ci_pct(result["per_race_yields"])
        if ci["n"] == 0:
            continue
        flag = ""
        if ci["hi"] > 0:
            flag = " *** CI+"
        elif ci["p0"] > 0.05:
            flag = " * P>5%"
        ci_str = f"[{ci['lo']:>+7.2%}, {ci['hi']:>+7.2%}]"
        print(f"  {display:<33} {result['n_races']:>5} {result['n_hits']:>5} "
              f"{result['hit_rate']:>8.2%} {result['avg_hit_payout']:>10,.0f} "
              f"{ci['roi']:>+9.2%} {ci_str:>30}  {ci['p0']:>5.1%}{flag}")


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # メイン検証: 各チルト戦略について多戦略比較
    # =========================================================
    strategies = [
        ("艇4 tilt 0.5-1.5 (まくり狙い)",
         "p.tilt_adjustment >= 0.5 AND p.tilt_adjustment <= 1.5", 4),
        ("艇5 tilt = 3.0 (大まくり)",
         "p.tilt_adjustment = 3.0", 5),
        ("艇5 tilt >= 1.5",
         "p.tilt_adjustment >= 1.5", 5),
        ("艇6 tilt >= 1.5",
         "p.tilt_adjustment >= 1.5", 6),
        ("艇5 tilt=3.0 + A2選手",
         "p.tilt_adjustment = 3.0 AND e.class_number = 2", 5),
        ("艇5 tilt=3.0 + A1+A2",
         "p.tilt_adjustment = 3.0 AND e.class_number <= 2", 5),
    ]

    for label, where, fixed in strategies:
        # WHERE 句に e.boat_number の制約を含める
        if "e.class_number" in where:
            where = where + f" AND e.boat_number = {fixed}"
        else:
            where = where + f" AND e.boat_number = {fixed}"
        # JOIN race_entries が必要
        # ところが get_strategy_data は GROUP_CONCAT で全艇取得するので
        # boat_number = fixed の絞り込みは別途必要

        # 修正: WHERE を「対象艇」の絞り込みだけにし、tilt 条件を扱う
        # シンプルにするため、where に "AND e.boat_number={fixed}" を含めるが、
        # GROUP_CONCAT は全艇含むので問題ない
        run_strategies(conn, label, where, fixed)

    conn.close()


if __name__ == "__main__":
    main()
